@echo off
setlocal
set "PATH=C:\Program Files\nodejs;%PATH%"
"C:\Program Files\nodejs\npx.cmd" --registry https://registry.npmjs.org chrome-devtools-mcp@1.4.0 %*
