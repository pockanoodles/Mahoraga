import { parseSSELine } from '../src/panel';

describe('parseSSELine', () => {
  it('returns null for empty lines', () => {
    expect(parseSSELine('')).toBeNull();
  });

  it('returns null for non-data lines', () => {
    expect(parseSSELine('event: message')).toBeNull();
    expect(parseSSELine(': keep-alive')).toBeNull();
    expect(parseSSELine('id: 42')).toBeNull();
  });

  it('parses a token event', () => {
    const line = 'data: {"type":"token","content":"Hello"}';
    expect(parseSSELine(line)).toEqual({ type: 'token', content: 'Hello' });
  });

  it('parses a tool_call event with extra fields', () => {
    const line = 'data: {"type":"tool_call","tool":"read_file","path":"app.py"}';
    expect(parseSSELine(line)).toEqual({
      type: 'tool_call',
      tool: 'read_file',
      path: 'app.py',
    });
  });

  it('parses a model event', () => {
    const line = 'data: {"type":"model","model":"qwen2.5-coder:7b"}';
    expect(parseSSELine(line)).toEqual({
      type: 'model',
      model: 'qwen2.5-coder:7b',
    });
  });

  it('parses a done event', () => {
    const line = 'data: {"type":"done"}';
    expect(parseSSELine(line)).toEqual({ type: 'done' });
  });

  it('parses an error event', () => {
    const line = 'data: {"type":"error","message":"ollama not running"}';
    expect(parseSSELine(line)).toEqual({
      type: 'error',
      message: 'ollama not running',
    });
  });

  it('returns null for malformed JSON', () => {
    const line = 'data: {invalid json}';
    expect(parseSSELine(line)).toBeNull();
  });

  it('returns null for data: with empty payload', () => {
    expect(parseSSELine('data: ')).toBeNull();
  });
});
