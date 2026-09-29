#!/bin/zsh
# Full probe: qwen3.5 both conditions first (the headline), then granite.
# run.py skips any (model, cond, task) that already has a result, so this is
# safe to relaunch after a crash.
cd "${0:A:h}"
PY="${0:A:h}/../../.venv/bin/python"
for arm in "qwen3.5:latest blind" "qwen3.5:latest feedback" \
           "granite4.1:8b blind" "granite4.1:8b feedback"; do
  echo "=== $arm $(date +%H:%M)"
  $PY run.py ${=arm}
done
echo "=== DONE $(date +%H:%M)"
