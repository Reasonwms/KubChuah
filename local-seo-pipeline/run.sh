#!/usr/bin/env bash
# CDO Consulting — Local SEO lead pipeline (Warren / Eastpointe, MI)  v1.0
#
#   ./run.sh city    48 searches: 24 categories x Warren + Eastpointe   (start here)
#   ./run.sh grid    8 high-value categories x 42 map cells (2 km)       (fuller coverage)
#   ./run.sh score   score the latest results only
#
# Needs: Docker (or set GMAPS_BIN=/path/to/google-maps-scraper), Python 3.
# Optional proxy:  export GMAPS_PROXIES="http://user:pass@host:port"
set -euo pipefail
cd "$(dirname "$0")"
MODE="${1:-city}"
STAMP="$(date +%Y%m%d-%H%M)"
mkdir -p output
export DISABLE_TELEMETRY=1                       # turn off the scraper's usage stats

# Warren + Eastpointe bounding box (minLat,minLon,maxLat,maxLon). Includes edges of
# Detroit, Roseville, Sterling Heights; the scorer filters by city afterwards.
BBOX="42.445,-83.090,42.537,-82.925"
CITIES="Warren,Eastpointe,Center Line"

scrape() {  # $1 = query file, $2 = results file, rest = extra flags
  local q="$1" out="$2"; shift 2
  local proxy=(); [[ -n "${GMAPS_PROXIES:-}" ]] && proxy=(-proxies "$GMAPS_PROXIES")
  local common=(-depth 5 -email -c 4 -exit-on-inactivity 5m ${proxy[@]+"${proxy[@]}"} "$@")
  if [[ -n "${GMAPS_BIN:-}" ]]; then
    "$GMAPS_BIN" -input "$q" -results "$out" "${common[@]}"
  else
    docker run --rm -e DISABLE_TELEMETRY=1 \
      -v gmaps-playwright-cache:/opt \
      -v "$PWD/queries:/queries:ro" -v "$PWD/output:/out" \
      gosom/google-maps-scraper \
      -input "/queries/$(basename "$q")" -results "/out/$(basename "$out")" "${common[@]}"
  fi
}

case "$MODE" in
  city) RES="output/results-city-$STAMP.csv"
        scrape queries/warren-eastpointe.txt "$RES" ;;
  grid) RES="output/results-grid-$STAMP.csv"
        scrape queries/grid-categories.txt "$RES" -grid-bbox "$BBOX" -grid-cell 2 ;;
  score) RES="$(ls -t output/results-*.csv | head -1)" ;;
  *) echo "usage: $0 [city|grid|score]"; exit 1 ;;
esac

python3 scripts/local_seo_score.py "$RES" --out "output/scored-$STAMP" --top 25 --cities "$CITIES"
echo
echo "Next: open output/scored-$STAMP/top-prospects.md"
echo "      Screaming Frog > Mode > List > upload output/scored-$STAMP/sf-urls.txt"
echo "      Claude Code: /sales-local 1"
