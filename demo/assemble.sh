#!/usr/bin/env bash
# demo/assemble.sh — cut the six generated shots against the six narration
# clips into the finished film.
#
#   ./demo/assemble.sh            # full film with narration
#   DRONE=0 ./demo/assemble.sh    # narration only, no sub-bass bed
#
# Inputs live in demo/raw/ (gitignored: large, regenerable, not the product).
# Output lands in demo/out/.
#
# The whole edit is a script rather than a project file for the same reason the
# vhs tapes are: when a number in the narration changes, the film is re-cut by
# re-running this, not reopened in an editor.
#
# Timing is driven by the NARRATION, not the footage. Each shot is stretched or
# trimmed to its own line plus a short tail, because the line is the content and
# the picture is illustration. Generated clips are capped at ~10s, so where a
# line runs longer the clip is slowed rather than looped — every shot here is
# either a constant-velocity stream or a slow camera move, and both survive
# retiming without reading as slow motion. Shot 6 is the extreme case (a 22s
# line over a 10s clip) and gets away with it because the field is static: only
# the camera moves, so 2.35x slower is simply a slower rise.
set -euo pipefail

cd "$(dirname "$0")/.."
RAW=demo/raw
OUT=demo/out
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"

DRONE=${DRONE:-1}

# shot : target segment length (narration + tail)
SEGMENTS=(
  "1:11.48"
  "2:10.59"
  "3:5.25"
  "4:9.59"
  "5:7.47"
  "6:23.55"
)

echo "Building segments..."
for entry in "${SEGMENTS[@]}"; do
  i=${entry%%:*}
  target=${entry##*:}
  src="$RAW/shot$i.mp4"
  vo="$RAW/vo$i.mp3"
  [ -f "$src" ] || { echo "missing $src" >&2; exit 1; }
  [ -f "$vo" ]  || { echo "missing $vo" >&2; exit 1; }

  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$src")
  # Slow the clip when the line outruns the footage; otherwise just trim.
  factor=$(python3 -c "print(max(1.0, $target/$dur))")

  ffmpeg -v error -y \
    -i "$src" -i "$vo" \
    -filter_complex "\
      [0:v]setpts=${factor}*PTS,scale=1280:720:flags=lanczos,\
           trim=duration=${target},setpts=PTS-STARTPTS,fps=24[v];\
      [1:a]afade=t=in:st=0:d=0.08,apad,atrim=duration=${target},\
           asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo[a]" \
    -map "[v]" -map "[a]" \
    -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p \
    -c:a aac -b:a 192k \
    "$WORK/seg$i.mp4"
  printf "  shot %s  %ss source -> %ss  (x%.3f)\n" "$i" "$dur" "$target" "$factor"
done

printf "file '%s'\n" "$WORK"/seg{1,2,3,4,5,6}.mp4 > "$WORK/list.txt"
ffmpeg -v error -y -f concat -safe 0 -i "$WORK/list.txt" -c copy "$WORK/joined.mp4"

TOTAL=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$WORK/joined.mp4")
echo "Joined: ${TOTAL}s"

# A film that is ~90% flat black is the worst case for h.264: without dither the
# dark falloff bands into visible rings, which is the single thing that makes
# abstract work look cheap. A very light grain costs almost nothing in bitrate
# and removes it. Fade from and to black at the ends.
FADE_OUT=$(python3 -c "print(round($TOTAL - 1.2, 3))")
VF="noise=alls=3:allf=t,fade=t=in:st=0:d=0.6,fade=t=out:st=${FADE_OUT}:d=1.2"

if [ "$DRONE" = "1" ]; then
  # Two detuned low sines under everything. Deliberately near-subliminal: it
  # fills the noise floor so the dry read does not sit on silence, and it is
  # mixed far enough down that it never competes with the narration.
  ffmpeg -v error -y \
    -i "$WORK/joined.mp4" \
    -f lavfi -t "$TOTAL" -i "sine=frequency=55:sample_rate=48000" \
    -f lavfi -t "$TOTAL" -i "sine=frequency=82.5:sample_rate=48000" \
    -filter_complex "\
      [1:a][2:a]amix=inputs=2,lowpass=f=180,volume=0.055,\
        afade=t=in:st=0:d=2,afade=t=out:st=${FADE_OUT}:d=1.2[bed];\
      [0:a][bed]amix=inputs=2:duration=first:normalize=0[a];\
      [0:v]${VF}[v]" \
    -map "[v]" -map "[a]" \
    -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p \
    -c:a aac -b:a 192k -movflags +faststart \
    "$OUT/mahoraga-cascade.mp4"
else
  ffmpeg -v error -y -i "$WORK/joined.mp4" \
    -vf "$VF" \
    -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p \
    -c:a copy -movflags +faststart \
    "$OUT/mahoraga-cascade.mp4"
fi

# Silent README loop: shots 3-4, the local answer and the judge catching it.
# No audio track at all — GitHub autoplays muted, and a silent file is smaller.
printf "file '%s'\n" "$WORK"/seg{3,4}.mp4 > "$WORK/loop.txt"
ffmpeg -v error -y -f concat -safe 0 -i "$WORK/loop.txt" \
  -an -vf "noise=alls=3:allf=t" \
  -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p -movflags +faststart \
  "$OUT/cascade-loop.mp4"

echo
echo "Wrote:"
ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/mahoraga-cascade.mp4" \
  | xargs -I{} echo "  $OUT/mahoraga-cascade.mp4  {}s"
ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/cascade-loop.mp4" \
  | xargs -I{} echo "  $OUT/cascade-loop.mp4      {}s"
