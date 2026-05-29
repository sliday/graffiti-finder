#!/usr/bin/env bash
# Stage everything needed for the Cloudflare Pages deploy into ./public.
# Only assets actually referenced by index.html + demo/map.html are copied;
# detection sweep outputs and the 71 MB samples directory are skipped.

set -euo pipefail

cd "$(dirname "$0")/.."

# Cache-buster: ties asset URLs in the HTML to the current git commit
# so a redeploy invalidates any 404 the browser cached from an earlier
# deploy in flight.
CACHE_BUST="?v=$(git rev-parse --short HEAD 2>/dev/null || date +%s)"

PUBLIC=public
rm -rf "$PUBLIC"
mkdir -p "$PUBLIC/data/images/crops" \
         "$PUBLIC/data/queue_viz/3689129454680282" \
         "$PUBLIC/data/queue_viz/453477123994712" \
         "$PUBLIC/data/runs/browser_fill/5c904830d8c16e2f573a0816afdc5a5c" \
         "$PUBLIC/data/samples" \
         "$PUBLIC/demo"

# Rewrite asset URLs in index.html with the cache-buster (only relative
# data/... and demo/map.html refs; external https://… untouched).
sed -E \
    -e 's|(src=")(data/[^"?]+)(")|\1\2'"$CACHE_BUST"'\3|g' \
    -e 's|(href=")(demo/map\.html)(")|\1\2'"$CACHE_BUST"'\3|g' \
    index.html > "$PUBLIC/index.html"
cp demo/map.html                           "$PUBLIC/demo/"
cp demo/writers.html                       "$PUBLIC/" 2>/dev/null || true
cp data/queue_gallery.html                 "$PUBLIC/data/"
cp data/samples/gallery.html               "$PUBLIC/data/samples/"
cp data/samples/sample_0[0-7]_*.jpg        "$PUBLIC/data/samples/"

# Crops referenced from the materials table.
for f in 1237359060636431_01 1237359060636431_02 \
         3689129454680282_01 3689129454680282_04 3689129454680282_06; do
  cp "data/images/crops/${f}.jpg" "$PUBLIC/data/images/crops/" 2>/dev/null || true
done

# Overlay images embedded in the explore section.
cp data/queue_viz/3689129454680282/overlay.jpg "$PUBLIC/data/queue_viz/3689129454680282/"
cp data/queue_viz/453477123994712/overlay.jpg  "$PUBLIC/data/queue_viz/453477123994712/"

# CAPTCHA screenshot used in #captcha.
cp data/runs/browser_fill/5c904830d8c16e2f573a0816afdc5a5c/11_ready_to_submit_NO_CLICK.png \
   "$PUBLIC/data/runs/browser_fill/5c904830d8c16e2f573a0816afdc5a5c/" 2>/dev/null || true

# Headers + redirects — Pages reads these from the root of the deploy dir.
cat > "$PUBLIC/_headers" <<'EOF'
/*
  Referrer-Policy: strict-origin-when-cross-origin
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Content-Security-Policy: default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline' https://unpkg.com; script-src 'self' 'unsafe-inline' https://unpkg.com; frame-src https://www.mapillary.com https://docs.google.com https://www.google.com; connect-src 'self' https://*.basemaps.cartocdn.com https://*.openstreetmap.org
EOF

cat > "$PUBLIC/_redirects" <<'EOF'
/map        /demo/map.html  301
/demo       /              301
/demo/      /              301
EOF

# Robots: allow indexing the long-read; block the heavy crop directories.
cat > "$PUBLIC/robots.txt" <<'EOF'
User-agent: *
Allow: /
Disallow: /data/images/
Disallow: /data/queue_viz/
EOF

echo "public/ built:"
du -sh "$PUBLIC"
echo "files: $(find "$PUBLIC" -type f | wc -l | tr -d ' ')"
