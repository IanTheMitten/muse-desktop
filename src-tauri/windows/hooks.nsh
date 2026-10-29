; Decision 2: a running sidecar keeps its exe locked, so a reinstall can silently keep the old one (tauri-apps/tauri#15134). Stop it before files are copied.
!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'taskkill /F /T /IM muse-core.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /T /IM muse-worker.exe'
  Pop $0
!macroend

; Same lock on uninstall: a running exe would be left behind.
!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec 'taskkill /F /T /IM muse-core.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /T /IM muse-worker.exe'
  Pop $0
!macroend
