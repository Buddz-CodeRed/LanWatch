; =============================================================================
; FILE: Mrk1-Installer/LanWatch.iss  -  THE WINDOWS SETUP WIZARD (BUILD_GUIDE Step 45)
; -----------------------------------------------------------------------------
; Built with Inno Setup 6 (free): build_installer.ps1 runs  ISCC LanWatch.iss
; Makes:  Output\LanWatch-Setup-0.01.exe   (LanWatch + Python + the AI model)
;
; What the user sees:
;   1. Licence  2. Install folder  3. Options (start at sign-in, desktop icon)
;   4. Create the admin account (username + password)  5. Ready
;   6. Download Nmap from nmap.org (only if it isn't installed; checked
;      against its published SHA-256 fingerprint)
;   7. Install: copies LanWatch, runs Nmap's own installer (Npcap's licence
;      doesn't allow a silent install), creates the admin account, creates
;      the "LanWatch" sign-in task, and starts LanWatch
;   8. Finish: "Open LanWatch now" opens the dashboard in the browser
;
; Where things go:
;   C:\Program Files\LanWatch\        the program (read-only)
;   C:\ProgramData\LanWatch\          your data: lanwatch.db, logs (app/paths.py)
;   Task Scheduler "LanWatch"         starts LanWatch with Administrator rights
;                                     at sign-in, without a permission prompt
; =============================================================================

#define AppName      "LanWatch"
#define AppVersion   "0.01"
#define NmapFile     "nmap-7.993-setup.exe"
#define NmapUrl      "https://nmap.org/dist/nmap-7.993-setup.exe"
; published at https://nmap.org/dist/sigs/nmap-7.993-setup.exe.digest.txt
#define NmapSha256   "22e5928d1738a1b94ffd3643014f52cb87d467dbec4cff9e5f721fe0b3782f1b"

[Setup]
AppId={{5B7C2E1A-4F3D-4C8B-9E2A-7D1F6A3B9C40}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} MrK1 {#AppVersion}
AppPublisher=LanWatch
AppComments=Home network monitoring system, hosted on this computer
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=Output
OutputBaseFilename=LanWatch-Setup-{#AppVersion}
SetupIconFile=assets\lanwatch.ico
UninstallDisplayIcon={app}\LanWatch.exe
UninstallDisplayName={#AppName} MrK1
LicenseFile=LICENSE.txt
WizardStyle=modern
; the AI model (~1.1 GB) barely compresses, so it is stored as-is (see [Files])
Compression=lzma2/normal
SolidCompression=no
CloseApplications=no

[Messages]
FinishedHeadingLabel=LanWatch is installed
FinishedLabel=LanWatch is running on this computer. Open it any time from the LanWatch shortcut: it opens in your web browser at http://127.0.0.1:5000.%n%nThe admin password you chose protects the System Settings and marking alerts as resolved.

[Tasks]
Name: "autostart"; Description: "Start LanWatch automatically when I sign in to Windows"; GroupDescription: "Starting LanWatch:"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; the program folder built by PyInstaller (LanWatch.spec), without the model...
Source: "dist\LanWatch\*"; DestDir: "{app}"; Excludes: "*.gguf"; Flags: ignoreversion recursesubdirs createallsubdirs
; ...which is copied separately, uncompressed (it doesn't shrink, and this is much faster)
Source: "dist\LanWatch\_internal\models\*.gguf"; DestDir: "{app}\_internal\models"; Flags: ignoreversion nocompression
Source: "scripts\register_task.ps1"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; "--open": open the dashboard, starting LanWatch first if needed (launcher.py)
Name: "{autoprograms}\LanWatch"; Filename: "{app}\LanWatch.exe"; Parameters: "--open"; Comment: "Open LanWatch in your browser"
Name: "{autodesktop}\LanWatch"; Filename: "{app}\LanWatch.exe"; Parameters: "--open"; Comment: "Open LanWatch in your browser"; Tasks: desktopicon

[Run]
Filename: "{app}\LanWatch.exe"; Parameters: "--open"; Description: "Open LanWatch now"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\schtasks.exe"; Parameters: "/End /TN ""LanWatch"""; Flags: runhidden; RunOnceId: "EndTask"
Filename: "{sys}\taskkill.exe"; Parameters: "/IM LanWatch.exe /F"; Flags: runhidden; RunOnceId: "StopLanWatch"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""LanWatch"" /F"; Flags: runhidden; RunOnceId: "DeleteTask"
; the Windows Firewall rule LanWatch adds when "Phones and tablets" is on (app/services/phone_listener.py)
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""LanWatch - phones and tablets (home network)"""; Flags: runhidden; RunOnceId: "DeleteFirewallRule"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Code]
var
  AdminPage: TInputQueryWizardPage;
  DownloadPage: TDownloadWizardPage;
  NmapDownloaded: Boolean;

{ ---- helpers --------------------------------------------------------------- }

function NmapInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf32}\Nmap\nmap.exe')) or
            FileExists(ExpandConstant('{commonpf64}\Nmap\nmap.exe'));
end;

function DataExists: Boolean;
begin
  { an earlier LanWatch left its data (and probably an admin account) here }
  Result := FileExists(ExpandConstant('{commonappdata}\LanWatch\lanwatch.db'));
end;

function ValidUsername(const S: String): Boolean;
var
  I: Integer;
  C: Char;
begin
  { the same rule as LanWatch itself: 3-32 letters, numbers, . _ - }
  Result := (Length(S) >= 3) and (Length(S) <= 32);
  if Result then
    for I := 1 to Length(S) do
    begin
      C := S[I];
      if not (((C >= 'a') and (C <= 'z')) or ((C >= 'A') and (C <= 'Z')) or
              ((C >= '0') and (C <= '9')) or (C = '_') or (C = '.') or (C = '-')) then
      begin
        Result := False;
        Exit;
      end;
    end;
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure RunHidden(const FileName, Params: String; Wait: Boolean);
var
  Code: Integer;
begin
  if Wait then
    Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, Code)
  else
    Exec(FileName, Params, '', SW_HIDE, ewNoWait, Code);
end;

{ ---- the wizard pages ------------------------------------------------------ }

procedure InitializeWizard;
var
  Prompt: String;
begin
  if DataExists then
    Prompt := 'LanWatch has been set up on this computer before. Leave these boxes empty to keep ' +
              'the existing admin account, or fill them in to replace it (for example if the password was forgotten).'
  else
    Prompt := 'The admin password protects the System Settings and marking alerts as resolved.' + #13#10 +
              'Username: 3 to 32 letters, numbers, dots, dashes or underscores. Password: at least 8 characters.';

  AdminPage := CreateInputQueryPage(wpSelectTasks,
    'Create the admin account',
    'Choose the username and password that protect LanWatch''s settings.',
    Prompt);
  AdminPage.Add('Username:', False);
  AdminPage.Add('Password:', True);
  AdminPage.Add('Confirm password:', True);

  DownloadPage := CreateDownloadPage('Downloading Nmap',
    'LanWatch uses Nmap to scan your network. Downloading the official installer from nmap.org...',
    @OnDownloadProgress);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  User, Pass, Confirm: String;
begin
  Result := True;

  { check the admin account details before moving on }
  if CurPageID = AdminPage.ID then
  begin
    User := Trim(AdminPage.Values[0]);
    Pass := AdminPage.Values[1];
    Confirm := AdminPage.Values[2];
    if DataExists and (User = '') and (Pass = '') and (Confirm = '') then
      Exit;                                   { reinstall: keep the existing account }
    if not ValidUsername(User) then
    begin
      MsgBox('The username must be 3 to 32 characters: letters, numbers, dots, dashes or underscores.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if (Length(Pass) < 8) or (Length(Pass) > 128) then
    begin
      MsgBox('The password must be 8 to 128 characters.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if Pass <> Confirm then
    begin
      MsgBox('The two passwords don''t match.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end

  { after "Install" is pressed: download Nmap first if it isn't installed }
  else if (CurPageID = wpReady) and not NmapInstalled then
  begin
    DownloadPage.Clear;
    DownloadPage.Add('{#NmapUrl}', '{#NmapFile}', '{#NmapSha256}');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;                { fails if the SHA-256 doesn't match }
        NmapDownloaded := True;
      except
        if DownloadPage.AbortedByUser then
          Log('Nmap download cancelled by the user.')
        else
          SuppressibleMsgBox('Nmap couldn''t be downloaded: ' + GetExceptionMessage + #13#10#13#10 +
            'LanWatch will still be installed. Install Nmap later from https://nmap.org/download.html ' +
            '(LanWatch''s dashboard will remind you).', mbInformation, MB_OK, IDOK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

{ ---- before copying files: stop a running LanWatch (reinstall / update) --- }

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  RunHidden(ExpandConstant('{sys}\schtasks.exe'), '/End /TN "LanWatch"', True);
  RunHidden(ExpandConstant('{sys}\taskkill.exe'), '/IM LanWatch.exe /F', True);
  Result := '';
end;

{ ---- after copying files: Nmap, admin account, sign-in task, start -------- }

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Lines: TArrayOfString;
  AdminFile, Args: String;
begin
  if CurStep <> ssPostInstall then
    Exit;

  { 1. Nmap + Npcap: their own installer, with its own screens }
  if NmapDownloaded then
  begin
    WizardForm.StatusLabel.Caption := 'Installing Nmap...';
    SuppressibleMsgBox('Nmap''s own installer opens next.' + #13#10#13#10 +
      'Click through it and keep "Npcap" ticked: LanWatch needs both to scan your network and measure traffic.',
      mbInformation, MB_OK, IDOK);
    if not Exec(ExpandConstant('{tmp}\{#NmapFile}'), '', '', SW_SHOW, ewWaitUntilTerminated, Code) then
      MsgBox('The Nmap installer couldn''t be started. Install Nmap later from https://nmap.org/download.html.', mbError, MB_OK);
  end;

  { 2. the admin account: details passed in a temporary file (not on the
       command line, where other programs could read them); LanWatch.exe
       deletes it as soon as it has read it }
  if Trim(AdminPage.Values[0]) <> '' then
  begin
    WizardForm.StatusLabel.Caption := 'Creating the admin account...';
    AdminFile := ExpandConstant('{tmp}\lanwatch-admin.txt');
    SetArrayLength(Lines, 2);
    Lines[0] := Trim(AdminPage.Values[0]);
    Lines[1] := AdminPage.Values[1];
    SaveStringsToUTF8File(AdminFile, Lines, False);
    if not Exec(ExpandConstant('{app}\LanWatch.exe'), '--create-admin "' + AdminFile + '"',
                ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
      Code := -1;
    DeleteFile(AdminFile);
    if Code <> 0 then
      MsgBox('The admin account couldn''t be created (code ' + IntToStr(Code) + ').' + #13#10 +
             'LanWatch will ask you to create it the first time it opens.', mbError, MB_OK);
  end;

  { 3. the sign-in task (Administrator rights without a prompt each time) }
  WizardForm.StatusLabel.Caption := 'Setting up how LanWatch starts...';
  Args := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\setup\register_task.ps1') +
          '" -AppDir "' + ExpandConstant('{app}') + '"';
  if WizardIsTaskSelected('autostart') then
    Args := Args + ' -AutoStart';
  if not Exec('powershell.exe', Args, '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
    MsgBox('LanWatch''s start-up task couldn''t be created (code ' + IntToStr(Code) + ').' + #13#10 +
           'You can still start LanWatch from its shortcut (Windows will ask for permission).', mbError, MB_OK);

  { 4. start LanWatch now, in the background }
  WizardForm.StatusLabel.Caption := 'Starting LanWatch...';
  RunHidden(ExpandConstant('{sys}\schtasks.exe'), '/Run /TN "LanWatch"', False);
end;

{ ---- uninstall: keep or delete the data ------------------------------------ }

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{commonappdata}\LanWatch');
    if DirExists(DataDir) and
       (SuppressibleMsgBox('Also delete your LanWatch data?' + #13#10#13#10 +
          'This removes the devices, scan history, alerts, settings and admin account in ' + DataDir + '.' + #13#10 +
          'Choose No to keep it for a future reinstall.',
          mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES) then
      DelTree(DataDir, True, True, True);
  end;
end;
