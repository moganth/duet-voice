; Duet Voice - NSIS installer script
;
; Builds DuetVoiceSetup.exe from the PyInstaller output. Run
; installer.bat first (from anywhere) - it freezes main.py with
; PyInstaller and copies the resulting DuetVoice.exe into this
; installer\ folder.
;
; Build with:  makensis installer.nsi
;
; This is a PER-USER installer (installs to %LOCALAPPDATA%, no admin
; required) since Duet Voice is a personal app, not shared system
; software.

!include "MUI2.nsh"

; ---------------------------------------------------------------------------
; Basic metadata
; ---------------------------------------------------------------------------
Name "Duet Voice"
OutFile "DuetVoiceSetup.exe"
InstallDir "$LOCALAPPDATA\DuetVoice"
InstallDirRegKey HKCU "Software\DuetVoice" "InstallDir"
RequestExecutionLevel user

; ---------------------------------------------------------------------------
; UI
; ---------------------------------------------------------------------------
!define MUI_ABORTWARNING
; Uncomment these two lines if you add your own app_icon.ico next to this script:
; !define MUI_ICON "app_icon.ico"
; !define MUI_UNICON "app_icon.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

; Launch Duet Voice right after Finish is clicked, so the tray icon
; shows up immediately instead of requiring a manual first launch.
; Checked by default - uncheck it yourself on the Finish page if you
; don't want it to launch right away.
!define MUI_FINISHPAGE_RUN "$INSTDIR\DuetVoice.exe"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ---------------------------------------------------------------------------
; Main install section
; ---------------------------------------------------------------------------
Section "Duet Voice (required)" SEC_MAIN
  SectionIn RO

  ; If Duet Voice is already running (e.g. reinstalling/upgrading over
  ; a live install), DuetVoice.exe below is locked and File would fail
  ; with "Error opening file for writing". Kill it first so this never
  ; happens, then give Windows a moment to release the file handle.
  ExecWait 'taskkill.exe /F /IM DuetVoice.exe /T'
  Sleep 500

  SetOutPath "$INSTDIR"

  ; The frozen app, staged here by installer.bat (which builds it with
  ; PyInstaller, copies it next to this script, then deletes dist\).
  File "DuetVoice.exe"

  ; Ship the real, already-configured settings.yaml (signaling server,
  ; display name, etc.) so the app works right after install - plus the
  ; example alongside it as a reference/reset point.
  SetOutPath "$INSTDIR\config"
  File "..\config\settings.yaml"
  File "..\config\settings.example.yaml"

  ; Bundled opus.dll - make sure you placed it per docs/SETUP.md before building
  SetOutPath "$INSTDIR\libs\windows"
  File "..\libs\windows\opus.dll"

  SetOutPath "$INSTDIR"

  ; Start Menu + Desktop shortcuts
  CreateDirectory "$SMPROGRAMS\Duet Voice"
  CreateShortcut "$SMPROGRAMS\Duet Voice\Duet Voice.lnk" "$INSTDIR\DuetVoice.exe"
  CreateShortcut "$SMPROGRAMS\Duet Voice\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\Duet Voice.lnk" "$INSTDIR\DuetVoice.exe"

  ; Remember install dir + write the uninstaller
  WriteRegStr HKCU "Software\DuetVoice" "InstallDir" "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Add/Remove Programs entry
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DuetVoice" \
      "DisplayName" "Duet Voice"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DuetVoice" \
      "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DuetVoice" \
      "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DuetVoice" \
      "DisplayIcon" "$INSTDIR\DuetVoice.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DuetVoice" \
      "Publisher" "Duet Voice"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DuetVoice" \
      "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DuetVoice" \
      "NoRepair" 1
SectionEnd

; ---------------------------------------------------------------------------
; Optional: launch on Windows startup
;
; --no-autoconnect: starts quietly in the tray and waits for you to
; click Connect yourself, rather than immediately trying to connect
; before you're necessarily ready to play.
; ---------------------------------------------------------------------------
Section "Start with Windows" SEC_STARTUP
  CreateShortcut "$SMSTARTUP\Duet Voice.lnk" "$INSTDIR\DuetVoice.exe" "--no-autoconnect"
SectionEnd

; ---------------------------------------------------------------------------
; Uninstaller
; ---------------------------------------------------------------------------
Section "Uninstall"
  ; Stop the running app first - otherwise DuetVoice.exe is locked and
  ; the Delete calls below silently fail to remove it.
  ExecWait 'taskkill.exe /F /IM DuetVoice.exe /T'
  Sleep 500

  Delete "$INSTDIR\DuetVoice.exe"
  RMDir /r "$INSTDIR\logs"
  Delete "$INSTDIR\config\settings.example.yaml"
  Delete "$INSTDIR\config\settings.yaml"        ; remove personal config too
  Delete "$INSTDIR\libs\windows\opus.dll"
  RMDir "$INSTDIR\libs\windows"
  RMDir "$INSTDIR\libs"
  RMDir "$INSTDIR\config"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"

  Delete "$SMPROGRAMS\Duet Voice\Duet Voice.lnk"
  Delete "$SMPROGRAMS\Duet Voice\Uninstall.lnk"
  RMDir "$SMPROGRAMS\Duet Voice"
  Delete "$SMSTARTUP\Duet Voice.lnk"
  Delete "$DESKTOP\Duet Voice.lnk"

  ; Registry cleanup - after files, but the app is already stopped
  ; above so ordering here has no locking implications either way.
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DuetVoice"
  DeleteRegKey HKCU "Software\DuetVoice"
SectionEnd

; ---------------------------------------------------------------------------
; Section descriptions (shown on the components page if you switch to
; MUI_PAGE_COMPONENTS instead of DIRECTORY - left simple by default above)
; ---------------------------------------------------------------------------
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_MAIN} "The Duet Voice application (required)."
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_STARTUP} "Launch Duet Voice automatically when Windows starts, ready in the tray (won't auto-connect - click Connect yourself when ready to play)."
!insertmacro MUI_FUNCTION_DESCRIPTION_END
