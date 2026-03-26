export const window = {
  createWebviewPanel: jest.fn(),
  registerWebviewViewProvider: jest.fn(),
};
export const workspace = {
  workspaceFolders: undefined as any,
};
export const Uri = {
  joinPath: jest.fn((_base: any, ...parts: string[]) => ({
    fsPath: parts.join('/'),
    toString: () => `vscode-resource:${parts.join('/')}`,
  })),
};
export const WebviewViewProvider = class {};
export const CancellationToken = class {};
