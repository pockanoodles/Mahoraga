module.exports = {
  projects: [
    {
      displayName: 'extension',
      preset: 'ts-jest',
      testEnvironment: 'node',
      testMatch: ['<rootDir>/test/*.test.ts'],
      moduleNameMapper: {
        vscode: '<rootDir>/test/__mocks__/vscode.ts',
      },
    },
    {
      displayName: 'webview',
      testEnvironment: 'jsdom',
      testMatch: ['<rootDir>/test/*.test.js'],
    },
  ],
};
