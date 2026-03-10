// hooks/useVoiceChat.ts
import { useCallback, useRef } from 'react';
import { useWebRTC, WebRTCHealthCallbacks } from './useWebRTC';
import { useChatMessages } from './useChatMessages';
import { useConnectionHealth, ReconnectionConfig } from './useConnectionHealth';
import { useServerHealth } from './useServerHealth';
import { useMicrophoneControl } from './useMicrophoneControl';
import { useVoiceKeywords } from './useVoiceKeywords';
import { useCancellation } from './useCancellation';
import { VoiceChatEvent } from '../models/VoiceChatEvent';
import { useTranscriptStore, SessionEndReason } from '../stores/transcriptStore';
import { DisconnectReason } from '../lib/connectionHealth';
import { voiceConfig } from '../config/voiceConfig';


export const useVoiceChat = () => {
    const { 
        connected, 
        connecting,
        error: connectionError,
        connectionState,
        dataChannelState,
        audioRef, 
        connect, 
        disconnect, 
        injectContext 
    } = useWebRTC();
    
    // Microphone control (mute, pause replies) - declare early for shouldAutoRespond
    const micControl = useMicrophoneControl({
        assistantName: voiceConfig.assistantName,  // Will be updated when server provides name
    });

    // Chat messages with auto-respond check based on mic state
    // Uses micControl.shouldAutoRespond which reads from ref to avoid stale closures
    const { messages, handleEvent, clearMessages, loadPreviousMessages, addSystemMessage } = useChatMessages({
        shouldAutoRespond: micControl.shouldAutoRespond,
    });
    
    // Server health monitoring (separate from WebRTC connection)
    const { 
        serverStatus, 
        consecutiveFailures: serverFailures,
        checkNow: checkServerNow,
        assistantName: serverAssistantName,
    } = useServerHealth();

    // Use server-provided assistant name if available, fall back to local config
    const assistantName = serverAssistantName || voiceConfig.assistantName;

    // Cancellation state and controls
    const cancellation = useCancellation({
        serverUrl: 'http://localhost:8080',
        onCancelComplete: () => {
            console.log('[VoiceChat] Cancellation completed');
        },
    });

    // Update microphone control with server-provided assistant name
    // (micControl declared earlier for shouldAutoRespond callback)

    // Track data channel for microphone control
    const dataChannelRef = useRef<RTCDataChannel | null>(null);

    // Helper to trigger a response via data channel (for voice keyword handlers)
    const triggerResponseViaDataChannel = useCallback(() => {
        const dc = dataChannelRef.current;
        if (dc && dc.readyState === 'open') {
            dc.send(JSON.stringify({ type: 'response.create' }));
            console.log('[VoiceChat] Triggered response via data channel');
        }
    }, []);

    // Voice keyword detection
    const voiceKeywords = useVoiceKeywords(
        {
            onPauseReplies: () => {
                console.log('[VoiceChat] Voice keyword: pause replies');
                micControl.pauseReplies();
                addSystemMessage('Replies paused - still listening', '⏸️');
                // Note: For pause via keyword, we don't trigger a response 
                // because the user explicitly wants silence
            },
            onResumeReplies: () => {
                console.log('[VoiceChat] Voice keyword: resume replies');
                micControl.resumeReplies();
                addSystemMessage('Replies resumed', '▶️');
                // Trigger a response so the model acknowledges the resume
                triggerResponseViaDataChannel();
            },
            onRespondNow: () => {
                console.log('[VoiceChat] Voice keyword: respond now');
                // Check state BEFORE triggerResponse (which may resume internally)
                const wasPaused = micControl.micState === 'paused';
                micControl.triggerResponse();
                // Show indicator if we were paused (triggerResponse resumes automatically)
                if (wasPaused) {
                    addSystemMessage('Replies resumed', '▶️');
                }
            },
            onMute: () => {
                console.log('[VoiceChat] Voice keyword: mute');
                if (!micControl.isMuted) {
                    micControl.toggleMute();
                }
            },
            onUnmute: () => {
                console.log('[VoiceChat] Voice keyword: unmute');
                if (micControl.isMuted) {
                    micControl.toggleMute();
                }
            },
        },
        { 
            assistantName,  // Use dynamic name from server or fallback
            enabled: voiceConfig.voiceKeywordsEnabled,
            debounceMs: voiceConfig.voiceKeywordDebounceMs,
        }
    );

    const { 
        sessionId, 
        createSession, 
        resumeSession, 
        syncToServer,
        endSession,
        clearSession 
    } = useTranscriptStore();

    // Connection health monitoring
    const health = useConnectionHealth({
        healthConfig: {
            idleWarningMs: 2 * 60 * 1000,        // 2 minutes
            sessionWarningMs: 55 * 60 * 1000,    // 55 minutes
            staleEventThresholdMs: 30 * 1000,    // 30 seconds no events
        },
        onReconnectNeeded: async () => {
            console.log('[VoiceChat] Auto-reconnect triggered');
            // Auto-reconnect uses the current session
            if (sessionId) {
                try {
                    await resumeVoiceChat(sessionId);
                } catch (err) {
                    console.error('[VoiceChat] Auto-reconnect failed:', err);
                }
            }
        },
    });

    // Create health callbacks to wire WebRTC events to health manager
    const createHealthCallbacks = useCallback((): WebRTCHealthCallbacks => ({
        onConnectionStateChange: (state) => {
            health.recordConnectionState(state);
        },
        onDataChannelStateChange: (state) => {
            if (state === 'open') {
                health.startSession();
            }
        },
        onEvent: (eventType) => {
            health.recordEvent(eventType);
            // User speech events count as activity
            if (eventType.includes('input_audio') || eventType.includes('speech')) {
                health.recordActivity();
            }
        },
        onAudioTrack: (track) => {
            // Wire up audio track to mic control for muting
            micControl.setAudioTrack(track);
            console.log('[VoiceChat] Audio track connected to mic control');
        },
        onDisconnect: (reason) => {
            const healthReason: DisconnectReason = 
                reason === 'user_initiated' ? 'user_initiated' :
                reason === 'connection_failed' ? 'connection_failed' :
                reason === 'data_channel_closed' ? 'data_channel_closed' :
                reason === 'ice_failed' ? 'network_error' : 'unknown';
            health.endSession(healthReason);
            
            // Map WebRTC disconnect reason to session end reason for tracking
            const sessionEndReason: SessionEndReason = 
                reason === 'user_initiated' ? 'user_ended' :
                reason === 'ice_failed' ? 'network_error' :
                reason === 'connection_failed' ? 'network_error' :
                reason === 'data_channel_closed' ? 'network_error' : 'error';
            
            // Record disconnect on server for analytics
            endSession(sessionEndReason, reason !== 'user_initiated' ? `WebRTC: ${reason}` : undefined);
        },
    }), [health]);

    // Process transcription events for voice keywords
    const processTranscriptionForKeywords = useCallback((text: string) => {
        if (text && text.length > 0) {
            voiceKeywords.processTranscription(text);
        }
    }, [voiceKeywords]);

    // Enhanced event handler that processes voice keywords and passes events to chat handler
    const createEventHandler = useCallback((dataChannel: RTCDataChannel) => {
        return (data: string) => {
            try {
                const event = JSON.parse(data) as VoiceChatEvent;

                // Check for transcription events (for keyword detection)
                if (event.type === 'conversation.item.input_audio_transcription.completed') {
                    const transcriptEvent = event as { transcript?: string };
                    if (transcriptEvent.transcript) {
                        processTranscriptionForKeywords(transcriptEvent.transcript);
                    }
                }

                // Pass all events to the regular handler
                handleEvent(event, dataChannel);
            } catch (err) {
                console.debug('Error parsing event:', err);
            }
        };
    }, [handleEvent, processTranscriptionForKeywords]);

    const startVoiceChat = async () => {
        try {
            // Create a new session for transcript tracking
            const newSessionId = await createSession();
            console.log('[VoiceChat] Created session:', newSessionId);
            
            await connect(
                (data: string, dataChannel: RTCDataChannel) => {
                    // Store data channel reference for microphone control
                    if (dataChannel !== dataChannelRef.current) {
                        dataChannelRef.current = dataChannel;
                        micControl.setDataChannel(dataChannel);
                    }
                    createEventHandler(dataChannel)(data);
                },
                undefined,  // No existing token for new session
                createHealthCallbacks()
            );
        } catch (err) {
            console.error('Error starting voice chat:', err);
        }
    };

    const resumeVoiceChat = async (sessionIdToResume: string) => {
        try {
            console.log('[VoiceChat] Resuming session:', sessionIdToResume);
            
            // Get resumption data (context + new OpenAI session)
            const resumeData = await resumeSession(sessionIdToResume);
            
            // Load previous messages into the UI for display
            if (resumeData.transcript && resumeData.transcript.length > 0) {
                console.log('[VoiceChat] Loading', resumeData.transcript.length, 'previous messages into UI');
                loadPreviousMessages(resumeData.transcript);
            }
            
            // Connect with the new OpenAI session
            await connect(
                (data: string, dataChannel: RTCDataChannel) => {
                    // Store data channel reference for microphone control
                    if (dataChannel !== dataChannelRef.current) {
                        dataChannelRef.current = dataChannel;
                        micControl.setDataChannel(dataChannel);
                    }
                    createEventHandler(dataChannel)(data);
                },
                resumeData.realtime.client_secret.value,
                createHealthCallbacks()
            );
            
            // Inject conversation context after connection is established
            if (resumeData.context_to_inject && resumeData.context_to_inject.length > 0) {
                console.log('[VoiceChat] Injecting', resumeData.context_to_inject.length, 'context items');
                // Small delay to ensure WebRTC is fully ready
                setTimeout(() => {
                    injectContext(resumeData.context_to_inject);
                }, 500);
            }
        } catch (err) {
            console.error('Error resuming voice chat:', err);
            throw err;
        }
    };

    const disconnectVoiceChat = async () => {
        // Sync any pending transcript entries before disconnecting
        await syncToServer();
        // End session with user_ended reason (explicit disconnect)
        await endSession('user_ended');
        disconnect();
    };

    // Config change handler for the experiment panel
    const setReconnectionConfig = useCallback((config: Partial<ReconnectionConfig>) => {
        health.setReconnectionConfig(config);
    }, [health]);

    return {
        // Connection state
        connected,
        connecting,
        connectionError,
        connectionState,
        dataChannelState,
        
        // Server health (separate from WebRTC)
        serverStatus,
        serverFailures,
        checkServerNow,
        
        // Chat state
        transcripts: messages,
        audioRef,
        sessionId,
        
        // Actions
        startVoiceChat,
        resumeVoiceChat,
        disconnectVoiceChat,
        clearMessages,
        clearSession,
        
        // Microphone control
        micState: micControl.micState,
        isMuted: micControl.isMuted,
        isPaused: micControl.isPaused,
        toggleMute: micControl.toggleMute,
        pauseReplies: micControl.pauseReplies,
        resumeReplies: micControl.resumeReplies,
        triggerResponse: micControl.triggerResponse,
        assistantName,  // Dynamic from server or fallback to local config
        
        // Health monitoring (for ConnectionExperimentPanel)
        healthStatus: health.healthStatus,
        sessionDuration: health.sessionDuration,
        idleTime: health.idleTime,
        timeSinceLastEvent: health.timeSinceLastEvent,
        lastDisconnectReason: health.lastDisconnectReason,
        reconnectCount: health.reconnectCount,
        isMonitoring: health.isMonitoring,
        eventLog: health.eventLog,
        reconnectionConfig: health.reconnectionConfig,
        setReconnectionConfig,
        
        // Cancellation controls
        cancelState: {
            isActive: cancellation.isActive,
            isCancelling: cancellation.isCancelling,
            runningTools: cancellation.runningTools,
            activeChildren: cancellation.activeChildren,
        },
        requestCancel: cancellation.requestCancel,
        handleCancelEvent: cancellation.handleEvent,
    };
};
