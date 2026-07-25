; Duet Voice - NSIS installer script
;
; Builds DuetVoiceSetup.exe from the PyInstaller output. Run this from
; the installer/ folder AFTER step 1 in docs/PACKAGING.md has produced
; ..\dist\DuetVoice.exe
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
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ---------------------------------------------------------------------------
; Main install section
; ---------------------------------------------------------------------------
Section "Duet Voice (required)" SEC_MAIN
  SectionIn RO
  SetOutPath "$INSTDIR"

  ; The packaged executable from PyInstaller (docs/PACKAGING.md step 1)
  File "..\dist\DuetVoice.exe"

  ; Ship the example config so first-run can copy it to settings.yaml
  SetOutPath "$INSTDIR\config"
  File "..\config\settings.example.yaml"

  ; Bundled opus.dll - make sure you placed it per docs/SETUP.md before building
  SetOutPath "$INSTDIR\libs\windows"
  File "..\libs\windows\opus.dll"

  SetOutPath "$INSTDIR"

  ; Start Menu shortcut
  CreateDirectory "$SMPROGRAMS\Duet Voice"
  CreateShortcut "$SMPROGRAMS\Duet Voice\Duet Voice.lnk" "$INSTDIR\DuetVoice.exe"
  CreateShortcut "$SMPROGRAMS\Duet Voice\Uninstall.lnk" "$INSTDIR\Uninstall.exe"

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
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DuetVoice" \
      "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DuetVoice" \
      "NoRepair" 1
SectionEnd

; ---------------------------------------------------------------------------
; Optional: launch on Windows startup
; ---------------------------------------------------------------------------
Section "Start with Windows" SEC_STARTUP
  CreateShortcut "$SMSTARTUP\Duet Voice.lnk" "$INSTDIR\DuetVoice.exe"
SectionEnd

; ---------------------------------------------------------------------------
; Uninstaller
; ---------------------------------------------------------------------------
Section "Uninstall"
  Delete "$INSTDIR\DuetVoice.exe"
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

  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DuetVoice"
  DeleteRegKey HKCU "Software\DuetVoice"
SectionEnd

; ---------------------------------------------------------------------------
; Section descriptions (shown on the components page if you switch to
; MUI_PAGE_COMPONENTS instead of DIRECTORY - left simple by default above)
; ---------------------------------------------------------------------------
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_MAIN} "The Duet Voice application (required)."
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_STARTUP} "Launch Duet Voice automatically when Windows starts, so it's ready before you open your game."
!insertmacro MUI_FUNCTION_DESCRIPTION_END
