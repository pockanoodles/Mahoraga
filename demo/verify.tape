# demo/verify.tape — regenerates docs/assets/verify.gif
#
# Run with: vhs demo/verify.tape   (brew install vhs)
#
# This is a script, not a screen recording, and that is the point: when a
# published number changes, the asset is re-rendered rather than re-shot. A
# one-off capture of a terminal rots the moment the output it shows does — and
# a stale demo of a *verification* command would be a particularly bad joke.
#
# It shows the cheap half of reproducibility: no models, no network, no API
# key, no GPU. Everything on screen runs on a fresh clone in about a second,
# which is the whole claim.

Output docs/assets/verify.gif

# Sized so the full output fits without scrolling. Two claim blocks plus the
# summary and the exit code run ~34 lines; at this font that needs ~940px of
# height. If a claim is added to experiments/claims.json, grow Height by ~13
# lines' worth or the top of the output scrolls off and the GIF shows a
# truncated result — which, for a verification demo, reads as a failure.
Set Shell "bash"
Set FontSize 18
Set Width 1360
Set Height 810
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms
Set PlaybackSpeed 1.0

# Land in the repo root with a clean, short prompt — the working directory is
# noise here, and a long prompt eats horizontal room the output needs.
#
# PATH is set explicitly: vhs spawns a non-interactive shell that never sources
# a profile, so ~/.local/bin (where `orch` lives) is absent and every command
# silently produces an empty frame.
Hide
Type `export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"`
Enter
Type "cd ~/Projects/Mahoraga"
Enter
Type `export PS1="$ "`
Enter
Type "clear"
Enter
Show

Sleep 800ms

Type "orch bench verify"
Sleep 400ms
Enter

# Long enough to read the two claim blocks without pausing the GIF.
Sleep 6s

Type "echo $?"
Sleep 300ms
Enter
Sleep 2s

Sleep 1s
