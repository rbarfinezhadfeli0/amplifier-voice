/**
 * Chat messages hook for managing voice conversation state.
 *
 * Handles:
 * - User transcription messages
 * - Assistant response streaming
 * - Voice event processing
 * (Tool execution is handled server-side through the sideband WebSocket)
 */

import { useRef, useState, useCallback } from 'react';
import { Message } from '../models/Message';
import { VoiceChatEvent, VoiceChatEventType } from '../models/VoiceChatEvent';
import { useTranscriptStore } from '../stores/transcriptStore';

// Tool name to friendly display name
const getFriendlyToolName = (toolName: string, toolArgs?: Record<string, unknown> | string): string => {
    const friendlyNames: Record<string, string> = {
        'bash': 'command line',
        'filesystem': 'file system',
        'read_file': 'reading file',
        'write_file': 'writing file',
        'list_directory': 'listing directory',
        'execute': 'running command',
        'web': 'web browser',
        'search': 'web search',
        'fetch': 'fetching URL',
    };

    // Special handling for delegate tool - extract agent name
    if (toolName === 'delegate' && toolArgs) {
        try {
            // Handle both string (JSON) and object arguments
            const args = typeof toolArgs === 'string' ? JSON.parse(toolArgs) : toolArgs;
            if (args.agent) {
                // Extract friendly agent name from "foundation:explorer" -> "explorer"
                const agentParts = (args.agent as string).split(':');
                const agentName = agentParts[agentParts.length - 1]
                    .replace(/-/g, ' ')
                    .replace(/_/g, ' ');
                return agentName;
            }
        } catch {
            // Fall through to default handling
        }
    }

    // Exact match
    if (friendlyNames[toolName]) {
        return friendlyNames[toolName];
    }

    // Partial match
    for (const [key, friendly] of Object.entries(friendlyNames)) {
        if (toolName.toLowerCase().includes(key)) {
            return friendly;
        }
    }

    // Default: make readable
    return toolName.replace(/_/g, ' ').replace(/-/g, ' ');
};

export interface UseChatMessagesOptions {
    /**
     * Callback to check if auto-response is allowed.
     * When returns false, transcription won't automatically trigger response.create.
     * Used for mute and listen modes.
     */
    shouldAutoRespond?: () => boolean;
}

export const useChatMessages = (options: UseChatMessagesOptions = {}) => {
    const { shouldAutoRespond } = options;
    
    const [messages, setMessages] = useState<Message[]>([]);
    const activeUserMessageRef = useRef<string | null>(null);
    const activeAssistantMessageRef = useRef<Message | null>(null);
    const dataChannelRef = useRef<RTCDataChannel | null>(null);
    
    // Async tool result handling - track response state and queue late-arriving results
    // This solves the issue where tool results arrive while the model is already speaking
    const responseInProgressRef = useRef<boolean>(false);
    const pendingToolAnnouncementsRef = useRef<Array<{ toolName: string; callId: string }>>([]);
    
    // Transcript capture
    const { addEntry: addTranscriptEntry } = useTranscriptStore();

    /**
     * Load previous transcript entries as messages for resumed sessions.
     * Called when resuming a session to show chat history in UI.
     */
    const loadPreviousMessages = useCallback((transcript: Array<{
        entry_type: string;
        text?: string;
        tool_name?: string;
        timestamp?: string;
    }>) => {
        const loadedMessages: Message[] = transcript
            .filter(entry => entry.text && (entry.entry_type === 'user' || entry.entry_type === 'assistant'))
            .map(entry => ({
                sender: entry.entry_type as 'user' | 'assistant',
                text: entry.text || '',
                timestamp: entry.timestamp ? new Date(entry.timestamp).getTime() : Date.now(),
                isStreaming: false,
                isHistory: true  // Mark as historical message
            }));
        
        console.log('[ChatMessages] Loaded', loadedMessages.length, 'previous messages');
        setMessages(loadedMessages);
    }, []);

    const sendToAssistant = useCallback((event: unknown) => {
        if (dataChannelRef.current?.readyState === 'open') {
            dataChannelRef.current.send(JSON.stringify(event));
        } else {
            console.warn('Data channel not ready');
        }
    }, []);

    /**
     * Flush pending tool announcements after response.done.
     * This triggers the model to speak about tool results that arrived while it was busy.
     */
    const flushPendingAnnouncements = useCallback(() => {
        if (pendingToolAnnouncementsRef.current.length === 0) return;
        
        const tools = pendingToolAnnouncementsRef.current.map(p => p.toolName).join(', ');
        const count = pendingToolAnnouncementsRef.current.length;
        console.log(`[ChatMessages] Flushing ${count} pending tool announcements: ${tools}`);
        pendingToolAnnouncementsRef.current = [];
        
        // Trigger model to speak about the completed tools
        sendToAssistant({
            type: 'response.create',
            response: {
                instructions: `The ${tools} task(s) completed while you were speaking. Please report those results now briefly.`
            }
        });
        responseInProgressRef.current = true;
    }, [sendToAssistant]);

    const handleEvent = useCallback(async (event: VoiceChatEvent, rtcDataChannel?: RTCDataChannel) => {
        // Store data channel reference
        if (rtcDataChannel) {
            dataChannelRef.current = rtcDataChannel;
        }

        // Debug logging - log ALL events to see what OpenAI is sending
        const eventTypesOfInterest = [
            'conversation.item.input_audio_transcription',
            'input_audio_buffer',
            'response.output',
            'response.audio',
            'error'
        ];
        if (eventTypesOfInterest.some(t => event.type.includes(t))) {
            console.log('[ChatMessages] Event:', event.type, event.transcript ? `transcript: "${event.transcript.substring(0, 50)}..."` : '');
        }
        
        // Log FULL error events - critical for debugging!
        if (event.type === 'error') {
            console.error('[ChatMessages] ERROR from OpenAI:', JSON.stringify(event, null, 2));
        }

        switch (event.type) {
            // Response lifecycle events - critical for async tool handling
            case 'response.created':
                // Model is starting a response - track state
                responseInProgressRef.current = true;
                console.log('[ChatMessages] Response started - blocking new response.create');
                break;

            case 'response.done':
                // Model finished responding - flush any queued tool announcements
                responseInProgressRef.current = false;
                console.log('[ChatMessages] Response done - checking for pending announcements');
                // Use setTimeout to avoid race conditions with tool results arriving
                setTimeout(() => {
                    if (pendingToolAnnouncementsRef.current.length > 0) {
                        flushPendingAnnouncements();
                    }
                }, 100);
                break;

            // User speech events
            case VoiceChatEventType.SPEECH_STARTED:
                if (!activeUserMessageRef.current) {
                    activeUserMessageRef.current = event.item_id || 'temp-id';
                    setMessages(prev => [...prev, {
                        sender: 'user',
                        text: '',
                        timestamp: Date.now(),
                        isStreaming: true
                    }]);
                }
                break;

            case VoiceChatEventType.TRANSCRIPTION_COMPLETED:
                if (activeUserMessageRef.current && event.transcript) {
                    const userText = event.transcript.trim();
                    setMessages(prev => prev.map(msg =>
                        msg.isStreaming && msg.sender === 'user'
                            ? { ...msg, text: userText, isStreaming: false }
                            : msg
                    ));
                    activeUserMessageRef.current = null;
                    
                    // Capture to transcript
                    addTranscriptEntry({
                        entry_type: 'user',
                        text: userText,
                        audio_duration_ms: event.audio_end_ms && event.audio_start_ms 
                            ? event.audio_end_ms - event.audio_start_ms 
                            : undefined,
                    });
                    
                    // With create_response: false, we manually trigger response
                    // The MODEL decides (via instructions) how much to say
                    // BUT check if auto-respond is allowed (mute/listen mode may disable)
                    const autoRespondAllowed = shouldAutoRespond ? shouldAutoRespond() : true;
                    if (dataChannelRef.current?.readyState === 'open' && autoRespondAllowed) {
                        console.log('[ChatMessages] Triggering response.create - model decides how to respond');
                        dataChannelRef.current.send(JSON.stringify({ type: 'response.create' }));
                    } else if (!autoRespondAllowed) {
                        console.log('[ChatMessages] Auto-respond blocked (muted or replies paused)');
                    }
                }
                break;

            // Assistant response events - handle BOTH text and audio transcript events
            // For voice responses, OpenAI sends response.output_audio_transcript.delta/done
            // For text responses, OpenAI sends response.output_text.delta/done
            case VoiceChatEventType.ASSISTANT_DELTA:
            case VoiceChatEventType.ASSISTANT_AUDIO_DELTA:
                if (event.delta) {
                    if (!activeAssistantMessageRef.current) {
                        const newMsg: Message = {
                            sender: 'assistant',
                            text: event.delta,
                            timestamp: Date.now(),
                            isStreaming: true
                        };
                        activeAssistantMessageRef.current = newMsg;
                        setMessages(prev => [...prev, newMsg]);
                    } else {
                        activeAssistantMessageRef.current = {
                            ...activeAssistantMessageRef.current,
                            text: activeAssistantMessageRef.current.text + event.delta,
                            timestamp: Date.now()
                        };
                        setMessages(prev => prev.map(msg =>
                            msg.isStreaming && msg.sender === 'assistant'
                                ? { ...activeAssistantMessageRef.current! }
                                : msg
                        ));
                    }
                }
                break;

            case VoiceChatEventType.ASSISTANT_DONE:
            case VoiceChatEventType.ASSISTANT_AUDIO_DONE:
                if (activeAssistantMessageRef.current) {
                    const assistantText = event.transcript || activeAssistantMessageRef.current.text;
                    const finalMsg: Message = {
                        sender: 'assistant',
                        text: assistantText,
                        timestamp: Date.now(),
                        isStreaming: false
                    };
                    setMessages(prev => prev.map(msg =>
                        msg.isStreaming && msg.sender === 'assistant' ? finalMsg : msg
                    ));
                    activeAssistantMessageRef.current = null;
                    
                    // Capture to transcript
                    if (assistantText) {
                        addTranscriptEntry({
                            entry_type: 'assistant',
                            text: assistantText,
                        });
                    }
                }
                break;

            // Amplifier voice events
            case VoiceChatEventType.VOICE_TOOL_START:
                if (event.data) {
                    const data = event.data;
                    const toolName = data.tool_name || 'unknown';
                    setMessages(prev => [...prev, {
                        sender: 'system',
                        text: data.spoken_text || `Using ${getFriendlyToolName(toolName)}`,
                        timestamp: Date.now(),
                        isSystem: true,
                        type: 'tool_status',
                        toolName,
                        toolStatus: 'executing'
                    }]);
                }
                break;

            case VoiceChatEventType.VOICE_TOOL_COMPLETE:
                if (event.data) {
                    const data = event.data;
                    const toolName = data.tool_name || 'unknown';
                    const success = data.success !== false;
                    const spokenText = data.spoken_text;
                    setMessages(prev => prev.map(msg =>
                        msg.type === 'tool_status' && msg.toolName === toolName && msg.toolStatus === 'executing'
                            ? {
                                ...msg,
                                text: spokenText || (success ? 'Done' : 'Completed with issues'),
                                toolStatus: success ? 'completed' : 'error',
                                isError: !success
                            }
                            : msg
                    ));
                }
                break;

            case VoiceChatEventType.VOICE_TOOL_ERROR:
                if (event.data) {
                    const data = event.data;
                    const toolName = data.tool_name || 'unknown';
                    const errorText = data.spoken_text || data.error || 'An error occurred';
                    setMessages(prev => prev.map(msg =>
                        msg.type === 'tool_status' && msg.toolName === toolName && msg.toolStatus === 'executing'
                            ? {
                                ...msg,
                                text: errorText,
                                toolStatus: 'error',
                                isError: true
                            }
                            : msg
                    ));
                }
                break;

            case VoiceChatEventType.VOICE_DISPLAY:
                if (event.data?.message) {
                    const data = event.data;
                    const displayText = data.spoken_text || data.message || '';
                    const displayLevel = data.level || 'info';
                    setMessages(prev => [...prev, {
                        sender: 'system',
                        text: displayText,
                        timestamp: Date.now(),
                        isSystem: true,
                        type: 'display',
                        displayLevel,
                        isError: displayLevel === 'error'
                    }]);
                }
                break;
        }
    }, [flushPendingAnnouncements, addTranscriptEntry, shouldAutoRespond]);

    const clearMessages = useCallback(() => {
        setMessages([]);
        activeUserMessageRef.current = null;
        activeAssistantMessageRef.current = null;
    }, []);

    // Add a system message (for visual indicators like pause/resume)
    const addSystemMessage = useCallback((text: string, icon?: string) => {
        const message: Message = {
            sender: 'system',
            text: icon ? `${icon} ${text}` : text,
            timestamp: Date.now(),
        };
        setMessages(prev => [...prev, message]);
    }, []);

    return {
        messages,
        handleEvent,
        clearMessages,
        loadPreviousMessages,
        addSystemMessage
    };
};
