; Inno Setup script. Per-user install: no administrator rights, no UAC prompt.
; Build:  iscc /DAppVersion=0.1.0 packaging\windows\installer.iss   (after PyInstaller has produced dist\dueclock-connector)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1C2D7E-4B1A-4E5B-9C0D-5A1D0C10C001}
AppName=DueClock Connector
AppVersion={#AppVersion}
AppPublisher=DueClock
DefaultDirName={localappdata}\DueClock\bin
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=DueClockConnector-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=DueClock Connector
; Unsigned for pilots (decision D-03). SmartScreen will warn: see SMOKE_TEST.md.

[Files]
Source: "..\..\dist\dueclock-connector\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Code]
var
  Page: TInputQueryWizardPage;

function JsonEscape(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

procedure InitializeWizard;
begin
  Page := CreateInputQueryPage(wpWelcome, 'Connect to DueClock',
    'Enter the details sent to you by DueClock.', 'The access key is stored encrypted for this Windows user only.');
  Page.Add('Cloud address (starts with https://):', False);
  Page.Add('Access key:', True);
  Page.Add('Tally port (usually 9000):', False);
  Page.Values[0] := 'https://';
  Page.Values[2] := '9000';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = Page.ID then
  begin
    if (Pos('https://', LowerCase(Trim(Page.Values[0]))) <> 1) or (Length(Trim(Page.Values[1])) < 20) then
    begin
      MsgBox('Please enter the full https address and the complete access key.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function RunConnector(const Args: String; var Code: Integer): Boolean;
begin
  Result := Exec(ExpandConstant('{app}\dueclock-connector.exe'), Args, '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Params, Tmp: String;
  Code: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    { Answers go through a temporary file, never the command line (other programs can read command lines). The connector wipes the file after reading. }
    Tmp := ExpandConstant('{tmp}\dc_setup.json');
    Params := '{"cloud_url":"' + JsonEscape(Trim(Page.Values[0])) + '","api_key":"' + JsonEscape(Trim(Page.Values[1])) + '","tally_port":' + Trim(Page.Values[2]) + '}';
    SaveStringToFile(Tmp, Params, False);
    if (not RunConnector('setup --from-file "' + Tmp + '"', Code)) or (Code <> 0) then
    begin
      MsgBox('Setup could not be completed (the access key may be wrong or incomplete). DueClock was installed but is not connected. Run the installer again with the correct key.', mbError, MB_OK);
      Exit;
    end;
    if (not RunConnector('install-task', Code)) or (Code <> 0) then
      MsgBox('The hourly background task could not be created. Run "dueclock-connector install-task" or contact DueClock.', mbError, MB_OK);
    { Best effort: find out which read-only requests this Tally accepts. Results are saved to the config; a failure here never blocks the install. }
    RunConnector('tally-check --save-profile', Code);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
    Exec(ExpandConstant('{app}\dueclock-connector.exe'), 'remove-task', '', SW_HIDE, ewWaitUntilTerminated, Code);
  if CurUninstallStep = usPostUninstall then
    if MsgBox('Also delete the connector''s saved settings and queue? (Your Tally data is never touched.)', mbConfirmation, MB_YESNO) = IDYES then
      DelTree(ExpandConstant('{localappdata}\DueClock'), True, True, True);
end;
