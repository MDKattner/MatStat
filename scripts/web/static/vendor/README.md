# Vendored browser libraries

These files are served as-is from `/static/vendor/`. MatStat has no bundler or
JavaScript package manager; the frontend is plain ES modules, so upgrades are
deliberate file replacements.

| File | Version | Upstream | License |
| --- | --- | --- | --- |
| `plotly.min.js` | 3.8.2 | <https://github.com/plotly/plotly.js> | MIT |
| `hls.min.js` | 1.5.13 | <https://github.com/video-dev/hls.js> | Apache-2.0 |

SHA-256:

```text
1d4105c9f8939d92710b1babf7d1636ca613bca4b3c03683dffc794ea1e916ee  plotly.min.js
e5753b10fc65eae35f21883e684d22c43ce66de968d35166792585b9b8a3f1a2  hls.min.js
```

## Updating

1. Download the minified release from upstream; do not rebuild it locally.
2. Replace the file, then update the version and hash table above.
3. Check that the upstream bundle still embeds its license notice.
4. Run `pytest -m browser` and manually verify the Pattern Map (Plotly) and a
   video preview (hls.js).
