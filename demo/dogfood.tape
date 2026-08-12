# demo/dogfood.tape — regenerates docs/assets/dogfood.gif
#
# Run with: vhs demo/dogfood.tape
#
# The two meters that describe what Mahoraga does for the person running it,
# rather than what it scores on a benchmark. Deterministic and free: both read
# local state, no inference.
#
# `usage` answers "of the work that arrived, how much stayed local".
# `funnel` answers the harder question one step earlier: "how much of the
# delegable work arrived at all". Shown together on purpose — usage alone can
# look excellent while the funnel says almost nothing was ever delegated, and
# that pairing is the honest picture.

Output docs/assets/dogfood.gif

Set Shell "bash"
Set FontSize 18
Set Width 1280
Set Height 900
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms

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

Type "orch metrics usage --since 2026-08-11"
Sleep 400ms
Enter
Sleep 5s

Type "orch metrics funnel"
Sleep 400ms
Enter
Sleep 5s

Sleep 1s
