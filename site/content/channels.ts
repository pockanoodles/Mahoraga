// Where Mahoraga can be installed from. A channel shows its command as live
// only once it really works: flip `live` in the release that publishes it,
// never before (the release checklist in site/README.md says when).
//
// The PyPI name is not "mahoraga": that name belongs to an unrelated project
// (hingebase/mahoraga, a reverse proxy for Python mirrors). The command a user
// types stays `mahoraga`.
export const PYPI = "mahoraga-audit";
export const TAP = "pockanoodles/tap";
export const IMAGE = "ghcr.io/pockanoodles/mahoraga";
export const GIT = "https://github.com/pockanoodles/Mahoraga";

export interface Channel {
  id: string;
  name: string;
  platforms: { en: string; ja: string };
  cmd: string;
  live: boolean;
}

export const CHANNELS: Channel[] = [
  { id: "brew", name: "Homebrew", platforms: { en: "macOS, Linux", ja: "macOS、Linux" }, cmd: `brew install ${TAP}/mahoraga`, live: false },
  { id: "uv", name: "uv", platforms: { en: "macOS, Linux", ja: "macOS、Linux" }, cmd: `uv tool install ${PYPI}`, live: false },
  { id: "pipx", name: "pipx", platforms: { en: "macOS, Linux", ja: "macOS、Linux" }, cmd: `pipx install ${PYPI}`, live: false },
  { id: "pip", name: "pip", platforms: { en: "Python 3.12 or newer", ja: "Python 3.12 以降" }, cmd: `pip install ${PYPI}`, live: false },
  {
    id: "docker",
    name: "Docker",
    platforms: { en: "Ollama on the host", ja: "Ollama はホスト側" },
    // 127.0.0.1 only: the server has no login, so it must not face the network.
    cmd: `docker run --rm -p 127.0.0.1:8000:8000 ${IMAGE}`,
    live: false,
  },
  {
    id: "source",
    name: "From source",
    // A git install gets the engine without the prebuilt app (the wheel carries it).
    platforms: { en: "CLI and API, no app", ja: "CLI と API のみ" },
    cmd: `uv tool install git+${GIT}`,
    live: true,
  },
];

/** What to run after installing, in order. */
export const FIRST_STEPS = ["ollama pull qwen3.5:latest", "mahoraga demo", "mahoraga audit mine ~/code/your-repo"];

/** Set to a GitHub Sponsors (or Ko-fi, Liberapay) URL once one is set up. */
export const SPONSOR_URL: string | null = null;
