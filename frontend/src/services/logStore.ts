export interface LogEntry {
  timestamp: string;
  level: 'info' | 'warn' | 'error' | 'debug';
  message: string;
  data?: unknown;
}

const logs: LogEntry[] = [];

export function addLog(level: LogEntry['level'], message: string, data?: unknown): void {
  logs.push({
    timestamp: new Date().toISOString(),
    level,
    message,
    data,
  });
}

export function getLogs(): LogEntry[] {
  return [...logs];
}

export function clearLogs(): void {
  logs.length = 0;
}
