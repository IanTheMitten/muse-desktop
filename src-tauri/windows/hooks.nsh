; Decision 2: a running sidecar keeps its exe locked, so a reinstall can silently keep the old one (tauri-apps/tauri#15134). Stop it before files are copied.
!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'taskkill /F /T /IM muse-core.exe'
  Pop $0
!macroend
