#!/bin/sh
# Put the standard-title model (app/services/standard_titles.py) into
# models/standard_title/ for local dev and tests. It isn't in git.
#
#   one_off/fetch_standard_title_model.sh                  # from the local yabot.jobs-ml checkout
#   one_off/fetch_standard_title_model.sh gs://yabotjobs-ml-models/standard-title/<version>
set -e
cd "$(dirname "$0")/.."
SRC="${1:-../yabot.jobs-ml/standard-title/models/onnx_final}"
mkdir -p models/standard_title
case "$SRC" in
  gs://*) gcloud storage cp "$SRC/*" models/standard_title/ ;;
  *) cp "$SRC"/model.onnx "$SRC"/tokenizer.json "$SRC"/run.json "$SRC"/parity_cases.json models/standard_title/ ;;
esac
echo "Standard-title model in models/standard_title/:"
ls -la models/standard_title
