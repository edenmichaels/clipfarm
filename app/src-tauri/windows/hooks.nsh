; ClipFarm NSIS hooks (Tauri v2 "installerHooks").
;
; Where ClipFarm keeps your data:
;   %APPDATA%\ClipFarm            settings, database, clips index, encrypted key backup (vault.dat)
;   Windows Credential Manager     API keys and sign-ins, service name "ClipFarm"
; Neither is inside the install folder, so installing a new version over the top, updating, or
; uninstalling leaves them alone.
;
; The uninstaller's "Delete the application data" box (unticked by default) is the only thing that
; removes them: when it is ticked, the engine wipes ClipFarm's Credential Manager entries and the
; %APPDATA%\ClipFarm folder before the program files are removed. Updates run the uninstaller in
; update mode, where the box is never shown or ticked.

!macro NSIS_HOOK_PREUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
    DetailPrint "Removing ClipFarm settings and keys..."
    nsExec::ExecToLog '"$INSTDIR\clipfarm-engine.exe" --wipe-user-data'
    Pop $0
    RMDir /r "$APPDATA\ClipFarm"
  ${EndIf}
!macroend
