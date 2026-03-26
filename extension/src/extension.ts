import * as vscode from 'vscode';
import { OllamaPanel } from './panel';

export function activate(context: vscode.ExtensionContext): void {
  const provider = new OllamaPanel(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(OllamaPanel.viewType, provider)
  );
}

export function deactivate(): void {}
