// components/VoiceChat.tsx
import React, { useState } from 'react';
import {
    makeStyles,
    tokens
} from '@fluentui/react-components';
import { useVoiceChat } from '../hooks/useVoiceChat';
import { useAmplifierEvents } from '../hooks/useAmplifierEvents';
import { ControlsPanel } from './ControlsPanel';
import { TranscriptDisplay } from './TranscriptDisplay';
import { SessionPicker } from './SessionPicker';
import { ConnectionExperimentPanel } from './ConnectionExperimentPanel';
import { MicrophoneControls } from './MicrophoneControls';
import { StopButton } from './StopButton';

const useStyles = makeStyles({
    wrapper: {
        width: '100%',
        height: '100vh',
        display: 'flex',
        backgroundColor: tokens.colorNeutralBackground1
    },
    container: {
        width: '100%',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
    },
    header: {
        display: 'flex',
        alignItems: 'center',
        gap: tokens.spacingHorizontalM,
        padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalL}`,
        borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
        backgroundColor: tokens.colorNeutralBackground2,
    },
    historyBtn: {
        padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalM}`,
        backgroundColor: tokens.colorNeutralBackground3,
        border: `1px solid ${tokens.colorNeutralStroke1}`,
        borderRadius: tokens.borderRadiusMedium,
        cursor: 'pointer',
        fontSize: tokens.fontSizeBase200,
        ':hover': {
            backgroundColor: tokens.colorNeutralBackground4,
        }
    },
    sessionId: {
        fontSize: tokens.fontSizeBase200,
        color: tokens.colorNeutralForeground3,
        fontFamily: 'monospace',
    },
    connectionStatus: {
        fontSize: tokens.fontSizeBase200,
        fontFamily: 'monospace',
        marginLeft: 'auto',
        display: 'flex',
        gap: tokens.spacingHorizontalS,
        alignItems: 'center',
    },
    statusBadge: {
        padding: `${tokens.spacingVerticalXXS} ${tokens.spacingHorizontalS}`,
        borderRadius: tokens.borderRadiusSmall,
        fontSize: tokens.fontSizeBase200,
    },
    statusConnected: {
        backgroundColor: tokens.colorPaletteGreenBackground2,
        color: tokens.colorPaletteGreenForeground2,
    },
    statusDisconnected: {
        backgroundColor: tokens.colorPaletteRedBackground2,
        color: tokens.colorPaletteRedForeground2,
    },
    statusConnecting: {
        backgroundColor: tokens.colorPaletteYellowBackground2,
        color: tokens.colorPaletteYellowForeground2,
    },
    statusDegraded: {
        backgroundColor: tokens.colorPaletteMarigoldBackground2,
        color: tokens.colorPaletteMarigoldForeground2,
    },
    errorBanner: {
        backgroundColor: tokens.colorPaletteRedBackground1,
        color: tokens.colorPaletteRedForeground1,
        padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalL}`,
        fontSize: tokens.fontSizeBase200,
    },
    content: {
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0 // Enables proper flex scrolling
    },
    controls: {
        boxSizing: 'border-box',
        padding: tokens.spacingHorizontalL,
        borderTop: `1px solid ${tokens.colorNeutralStroke1}`,
    },
    audio: {
        display: 'none'
    }
});

export const VoiceChat: React.FC = () => {
    const styles = useStyles();
    const [showSessionPicker, setShowSessionPicker] = useState(false);
    const { 
        connected,
        connecting,
        connectionError,
        connectionState,
        dataChannelState,
        // Server health (separate from WebRTC)
        serverStatus,
        transcripts, 
        audioRef, 
        sessionId,
        startVoiceChat, 
        resumeVoiceChat,
        disconnectVoiceChat,
        // Microphone control
        micState,
        toggleMute,
        pauseReplies,
        resumeReplies,
        triggerResponse,
        assistantName,
        // Health monitoring
        healthStatus,
        sessionDuration,
        idleTime,
        timeSinceLastEvent,
        lastDisconnectReason,
        reconnectCount,
        isMonitoring,
        eventLog,
        reconnectionConfig,
        setReconnectionConfig,
        // Cancellation
        cancelState,
        requestCancel,
        handleCancelEvent,
    } = useVoiceChat();

    // Connect SSE events to cancellation handler
    // This allows the UI to track tool execution state from Amplifier events
    useAmplifierEvents({
        autoConnect: true,
        logToConsole: true,
        onEvent: (event) => {
            // Pass relevant events to cancellation handler
            handleCancelEvent(event);
        },
    });

    const handleSessionSelect = async (selectedSessionId: string, isResume: boolean) => {
        if (isResume) {
            try {
                await resumeVoiceChat(selectedSessionId);
            } catch (err) {
                console.error('Failed to resume session:', err);
            }
        }
    };

    const handleNewSession = async () => {
        await startVoiceChat();
    };

    return (
        <div className={styles.wrapper}>
            <div className={styles.container}>
                {/* Header with session info and picker button */}
                <div className={styles.header}>
                    <button 
                        className={styles.historyBtn}
                        onClick={() => setShowSessionPicker(true)}
                        title="View session history"
                    >
                        📋 Sessions
                    </button>
                    {sessionId && (
                        <span className={styles.sessionId}>
                            Session: {sessionId.slice(0, 8)}...
                        </span>
                    )}
                    <div className={styles.connectionStatus}>
                        {/* Voice (WebRTC) connection status */}
                        <span 
                            className={`${styles.statusBadge} ${
                                connected ? styles.statusConnected : 
                                connecting ? styles.statusConnecting : 
                                styles.statusDisconnected
                            }`}
                            title={`Voice: ${connectionState || 'disconnected'}${dataChannelState ? ` | dc:${dataChannelState}` : ''}`}
                        >
                            {connected ? '🎤 Voice' : 
                             connecting ? '↻ Voice...' : 
                             '○ Voice'}
                        </span>
                        {/* Server connection status */}
                        <span 
                            className={`${styles.statusBadge} ${
                                serverStatus === 'connected' ? styles.statusConnected :
                                serverStatus === 'checking' ? styles.statusConnecting :
                                styles.statusDegraded
                            }`}
                            title={serverStatus === 'connected' 
                                ? 'Server connected - tools available' 
                                : serverStatus === 'checking'
                                ? 'Checking server...'
                                : 'Server unreachable - voice works but tools unavailable'}
                        >
                            {serverStatus === 'connected' ? '⚡ Tools' :
                             serverStatus === 'checking' ? '↻ Tools...' :
                             '⚠ Tools'}
                        </span>
                    </div>
                </div>
                
                {/* Error banner */}
                {connectionError && (
                    <div className={styles.errorBanner}>
                        ⚠️ {connectionError}
                    </div>
                )}
                
                <div className={styles.content}>
                    <TranscriptDisplay transcripts={transcripts} />
                </div>
                <div className={styles.controls}>
                    <ControlsPanel
                        connected={connected}
                        connecting={connecting}
                        onStart={startVoiceChat}
                        onDisconnect={disconnectVoiceChat}
                    />
                    
                    {/* Stop Button for cancelling operations */}
                    {connected && (
                        <div style={{ marginTop: tokens.spacingVerticalS }}>
                            <StopButton
                                isActive={cancelState.isActive}
                                isCancelling={cancelState.isCancelling}
                                runningTools={cancelState.runningTools}
                                activeChildren={cancelState.activeChildren}
                                onCancel={requestCancel}
                            />
                        </div>
                    )}
                    
                    {/* Microphone Controls (mute, pause replies) */}
                    <div style={{ marginTop: tokens.spacingVerticalM }}>
                        <MicrophoneControls
                            micState={micState}
                            connected={connected}
                            assistantName={assistantName}
                            onToggleMute={toggleMute}
                            onPauseReplies={pauseReplies}
                            onResumeReplies={resumeReplies}
                            onTriggerResponse={triggerResponse}
                        />
                    </div>
                    
                    {/* Connection Health Experiment Panel */}
                    <div style={{ marginTop: tokens.spacingVerticalM }}>
                        <ConnectionExperimentPanel
                            healthStatus={healthStatus}
                            sessionDuration={sessionDuration}
                            idleTime={idleTime}
                            timeSinceLastEvent={timeSinceLastEvent}
                            lastDisconnectReason={lastDisconnectReason}
                            reconnectCount={reconnectCount}
                            isMonitoring={isMonitoring}
                            eventLog={eventLog}
                            reconnectionConfig={reconnectionConfig}
                            onConfigChange={setReconnectionConfig}
                        />
                    </div>
                </div>
            </div>
            <audio ref={audioRef} autoPlay className={styles.audio} />
            
            <SessionPicker
                isOpen={showSessionPicker}
                onClose={() => setShowSessionPicker(false)}
                onSessionSelect={handleSessionSelect}
                onNewSession={handleNewSession}
            />
        </div>
    );
};
