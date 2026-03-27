import * as vscode from 'vscode';
import * as fs from 'fs';

const BACKEND_URL = 'http://localhost:11278';

export interface SseEvent {
  type: string;
  [key: string]: unknown;
}

/**
 * Parse a single SSE line into an event object.
 * Returns null for non-data lines or malformed JSON.
 * Exported for unit testing.
 */
export function parseSSELine(line: string): SseEvent | null {
  if (!line.startsWith('data: ')) {
    return null;
  }
  const payload = line.slice(6).trim();
  if (!payload) {
    return null;
  }
  try {
    return JSON.parse(payload) as SseEvent;
  } catch {
    return null;
  }
}

function getNonce(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  return Array.from({ length: 32 }, () =>
    chars.charAt(Math.floor(Math.random() * chars.length))
  ).join('');
}

export class OllamaPanel implements vscode.WebviewViewProvider {
  public static readonly viewType = 'ollama-runtime.panel';

  private _view?: vscode.WebviewView;

  constructor(private readonly _extensionUri: vscode.Uri) {}

  public resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this._extensionUri],
    };

    webviewView.webview.html = this._buildHtml(webviewView.webview);

    webviewView.webview.onDidReceiveMessage(async (msg: { type: string; message: string }) => {
      if (msg.type === 'chat') {
        await this._handleChat(msg.message);
      }
    });

    this._loadHistory(webviewView.webview);
  }

  private _buildHtml(webview: vscode.Webview): string {
    const nonce = getNonce();

    const uri = (...parts: string[]) =>
      webview.asWebviewUri(vscode.Uri.joinPath(this._extensionUri, 'webview', ...parts)).toString();

    const htmlPath = vscode.Uri.joinPath(this._extensionUri, 'webview', 'index.html').fsPath;
    let html = fs.readFileSync(htmlPath, 'utf8');

    html = html
      .replace(/\$\{nonce\}/g, nonce)
      .replace(/\$\{cspSource\}/g, webview.cspSource)
      .replace(/\$\{styleUri\}/g, uri('style.css'))
      .replace(/\$\{highlightCssUri\}/g, uri('atom-one-dark.min.css'))
      .replace(/\$\{highlightJsUri\}/g, uri('highlight.min.js'))
      .replace(/\$\{utilsJsUri\}/g, uri('utils.js'))
      .replace(/\$\{chatJsUri\}/g, uri('chat.js'));

    return html;
  }

  private async _loadHistory(webview: vscode.Webview): Promise<void> {
    const workspace = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '/tmp';
    try {
      const response = await fetch(`${BACKEND_URL}/history?workspace=${encodeURIComponent(workspace)}`);
      const data = await response.json() as { messages: Array<{ role: string; content: string }> };
      if (data.messages.length > 0) {
        webview.postMessage({ type: 'history', messages: data.messages });
      }
    } catch {
      // backend not running yet — panel will work, just no history
    }
  }

  private async _handleChat(message: string): Promise<void> {
    if (!this._view) {
      return;
    }

    const workspace =
      vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '/tmp';

    const post = (event: SseEvent) => this._view!.webview.postMessage(event);

    try {
      const response = await fetch(`${BACKEND_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, workspace }),
      });

      if (!response.body) {
        post({ type: 'error', message: 'No response body from backend' });
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        // Keep the last (possibly incomplete) line in the buffer
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          const event = parseSSELine(line);
          if (event) {
            post(event);
          }
        }
      }

      // Flush remaining buffer after stream ends
      if (buffer) {
        const event = parseSSELine(buffer);
        if (event) {
          post(event);
        }
      }
    } catch (error) {
      post({
        type: 'error',
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }
}
