# demo/cascade.tape — regenerates docs/assets/cascade.gif
#
# Run with: vhs demo/cascade.tape
#
# The serving path, live and in real time: a local 8B answers, a free local
# judge reads the answer without ever seeing the tests, and only a rejection
# reaches a paid arm.
#
# NOTE — this take is not deterministic, and that is a property of the system
# rather than a flaw in the tape. The judge's fail-recall is 0.688 and the local
# arm does not fail on demand, so the same prompt escalates on one run and is
# accepted on the next. Re-run until the take shows what you want to show; do
# not go hunting for a prompt that "always fails", because there isn't one and
# pretending otherwise would misrepresent the escalation rate.
#
# Escalation spends real money on the configured arm (~$0.04/call).

Output docs/assets/cascade.gif

Set Shell "bash"
Set FontSize 18
Set Width 1180
# 440 fits both outcomes: a kept-local result, and an escalated one, which
# adds two lines (arm + cost).
Set Height 440
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms

# 2x playback. The take is real and untrimmed; only its playback is compressed,
# because roughly two thirds of a cold local call is the model loading and a
# blank screen is not information.
Set PlaybackSpeed 4

Hide
Type `export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"`
Enter
Type "cd ~/Projects/Mahoraga"
Enter
Type `export PS1="$ "`
Enter
# Warm the arm off-camera. A cold granite spends ~20s loading before it emits a
# token; recording that would show the laptop's disk, not the router. The
# on-camera call is a warm one, which is also the steady state in real use.
Type `python3 demo/one_task.py "return the sum of a list" > /dev/null 2>&1`
Enter
Sleep 40s
Type "clear"
Enter
Show

Sleep 800ms

Type `python3 demo/one_task.py "Write count_smaller(nums) returning, for each element, how many elements to its right are smaller, in O(n log n)."`
Sleep 400ms
Enter

# Generous, because the wall time here is genuinely variable: granite (5.3 GB)
# and the qwen3.5 judge (6.6 GB) do not both fit in 16 GB, so every judged task
# swaps models and observed takes ranged 34-60s+. Sized for the slow case; the
# 4x playback absorbs the dead air.
Sleep 110s

Sleep 1s
