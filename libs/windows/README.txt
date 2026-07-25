Place a matching `opus.dll` here (i.e. `libs/windows/opus.dll`).

The DLL must match the Python process architecture: `x64` for the
default 64-bit Python install, or `x86` only if you are running
32-bit Python.

See ../../docs/SETUP.md step 2 for where to download it. This whole
`libs/` folder is gitignored on purpose (binary, platform-specific) -
this README is the one exception so the expected path is obvious.
