# Paper Shaders, bundled for Housewarm

`shaders.min.js` is [Paper Shaders](https://github.com/paper-design/shaders)
(`@paper-design/shaders` 0.0.39, MIT licence, see `LICENSE`) bundled with
`entry.js` into one file that sets `window.HousewarmShaders` with
`meshGradient(el, props)` and `pulsingBorder(el, props)`.

`entry.js` reproduces the defaults and prop-to-uniform mapping of
`@paper-design/shaders-react` 0.0.43, the React wrapper version whose props
(`spotsPerColor` and the rest) the hero design was written for, so every
parameter means what it meant there. No React, nothing fetched at runtime:
the noise textures are embedded data.

Rebuild:

    npm install --save-exact @paper-design/shaders@0.0.39 esbuild
    npx esbuild entry.js --bundle --minify --format=iife --legal-comments=none --outfile=shaders.min.js
