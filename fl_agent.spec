# -*- mode: python ; coding: utf-8 -*-
# fl_agent.spec
#
# PyInstaller spec that produces two executables in dist/:
#
#   dist/FL Agent/FL Agent.exe   — Tkinter GUI (windowed, no console)
#   dist/fl_mcp_server.exe       — MCP stdio server (single-file, console)
#
# The GitHub Actions workflow (build-installer.yml) copies fl_mcp_server.exe
# into dist/FL Agent/ before Inno Setup packages everything.
#
# Build:
#   pyinstaller fl_agent.spec [--clean]

block_cipher = None

# ---------------------------------------------------------------------------
# Shared settings
# ---------------------------------------------------------------------------
COMMON_HIDDEN = [
    # mido MIDI backends — not auto-discovered
    'mido.backends',
    'mido.backends.rtmidi',
    'mido.backends.portmidi',
    # tkinter sub-modules
    'tkinter',
    'tkinter.ttk',
    'tkinter.font',
    'tkinter.messagebox',
    # sounddevice ships its own PortAudio DLL on Windows
    'sounddevice',
    # MCP / OpenAI / Anthropic SDK internals
    'mcp',
    'mcp.server',
    'mcp.server.stdio',
    'mcp.types',
    'openai',
    'anthropic',
    # FastAPI / Uvicorn (used by music_api.py)
    'fastapi',
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'pydantic',
    # Numerics
    'numpy',
    'numpy.core',
]

COMMON_DATAS = [
    ('AGENTS.md', '.'),
    ('fl_studio_script', 'fl_studio_script'),
    # Local modules that PyInstaller may miss as pure-Python sources
    ('fl_transport.py',   '.'),
    ('midi_scheduler.py', '.'),
    ('midi_generator.py', '.'),
    ('music_api.py',      '.'),
    ('agent.py',          '.'),
]

# ---------------------------------------------------------------------------
# 1. GUI application — gui.py  (onedir, windowed)
# ---------------------------------------------------------------------------
gui_a = Analysis(
    ['gui.py'],
    pathex=['.'],
    binaries=[],
    datas=COMMON_DATAS,
    hiddenimports=COMMON_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data, cipher=block_cipher)

gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    [],
    exclude_binaries=True,       # binaries go into COLLECT → onedir mode
    name='FL Agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,               # no black console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

gui_coll = COLLECT(
    gui_exe,
    gui_a.binaries,
    gui_a.zipfiles,
    gui_a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FL Agent',             # → dist/FL Agent/
)

# ---------------------------------------------------------------------------
# 2. MCP server — fl_mcp_server.py  (onefile, console)
# ---------------------------------------------------------------------------
srv_a = Analysis(
    ['fl_mcp_server.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('AGENTS.md', '.'),
        ('fl_transport.py',   '.'),
        ('midi_scheduler.py', '.'),
        ('midi_generator.py', '.'),
        ('music_api.py',      '.'),
    ],
    hiddenimports=COMMON_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

srv_pyz = PYZ(srv_a.pure, srv_a.zipped_data, cipher=block_cipher)

# Onefile: pass binaries/zipfiles/datas directly into EXE, no COLLECT.
srv_exe = EXE(
    srv_pyz,
    srv_a.scripts,
    srv_a.binaries,
    srv_a.zipfiles,
    srv_a.datas,
    [],
    name='fl_mcp_server',        # → dist/fl_mcp_server.exe
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,                # MCP server communicates via stdio
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
