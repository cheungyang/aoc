<#
.SYNOPSIS
    One-time setup of "Agent Chrome" on Windows for the AOC browser agent.

.DESCRIPTION
    Creates three things, all removable with -Uninstall:

      1. An "Agent Chrome (AOC)" desktop shortcut. It starts Chrome with its own
         profile (separate cookies and logins) and the DevTools port on
         127.0.0.1:<ChromePort>. Opening it is how you let the agent book;
         closing the window takes it offline again.
      2. A port proxy (netsh interface portproxy, built into Windows) from
         0.0.0.0:<ListenPort> to 127.0.0.1:<ChromePort>. Chrome only listens on
         localhost; the proxy makes it reachable on the LAN.
      3. A Windows Firewall rule allowing <ListenPort> inbound ONLY from the NAS
         IP, on the given network profile(s). Chrome's DevTools port has no
         authentication: anyone who reaches it controls the browser and every
         site logged in there, so nothing but the NAS may connect.

    Run from an elevated PowerShell (Run as administrator):

      powershell -ExecutionPolicy Bypass -File .\Install-AgentChrome.ps1 -NasIp 192.168.1.10
      powershell -ExecutionPolicy Bypass -File .\Install-AgentChrome.ps1 -Uninstall

.PARAMETER NasIp
    LAN IPv4 address of the NAS (give it a DHCP reservation on the router).
.PARAMETER AutoStartAtLogon
    Also put the shortcut in the Startup folder. Off by default: you decide
    when the agent may use the browser.
.PARAMETER FirewallProfile
    Network profile(s) the firewall rule applies to. Default Private.
.PARAMETER RemoveProfile
    With -Uninstall, also delete the Agent Chrome profile (all its logins).
#>
[CmdletBinding()]
param(
    [string]$NasIp,
    [int]$ListenPort = 9223,
    [int]$ChromePort = 9222,
    [string]$ProfileDir = (Join-Path $env:LOCALAPPDATA 'AgentChrome'),
    [string[]]$FirewallProfile = @('Private'),
    [switch]$AutoStartAtLogon,
    [switch]$Uninstall,
    [switch]$RemoveProfile
)

$ErrorActionPreference = 'Stop'
$RuleName = 'AOC Agent Chrome (NAS only)'
$ShortcutName = 'Agent Chrome (AOC).lnk'
$DesktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) $ShortcutName
$StartupLink = Join-Path ([Environment]::GetFolderPath('Startup')) $ShortcutName

function Assert-Admin {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this from an elevated PowerShell (right-click > Run as administrator).'
    }
}

function Find-Chrome {
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) { return $path }
    }
    throw 'Google Chrome was not found. Install it, then run this script again.'
}

function Remove-PortProxy {
    netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$ListenPort 2>&1 | Out-Null
}

function New-AgentShortcut([string]$Path, [string]$Chrome) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $Chrome
    $link.Arguments = "--user-data-dir=`"$ProfileDir`" --remote-debugging-port=$ChromePort --no-first-run --no-default-browser-check"
    $link.WorkingDirectory = Split-Path $Chrome
    $link.IconLocation = "$Chrome,0"
    $link.Description = 'Chrome profile the AOC agent may operate. Close it to take the agent offline.'
    $link.Save()
}

Assert-Admin

if ($Uninstall) {
    Remove-PortProxy
    Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    foreach ($link in @($DesktopLink, $StartupLink)) {
        if (Test-Path $link) { Remove-Item $link -Force }
    }
    if ($RemoveProfile -and (Test-Path $ProfileDir)) {
        Remove-Item $ProfileDir -Recurse -Force
        Write-Host "Deleted profile $ProfileDir (all Agent Chrome logins)."
    }
    Write-Host 'Agent Chrome setup removed (port proxy, firewall rule, shortcuts).'
    return
}

$parsed = $null
if (-not $NasIp -or -not [System.Net.IPAddress]::TryParse($NasIp, [ref]$parsed) -or
    $parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
    throw 'Pass the NAS LAN IPv4 address, e.g. -NasIp 192.168.1.10'
}

$chrome = Find-Chrome
New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null

# 1. Shortcut(s)
New-AgentShortcut -Path $DesktopLink -Chrome $chrome
Write-Host "Shortcut: $DesktopLink"
if ($AutoStartAtLogon) {
    New-AgentShortcut -Path $StartupLink -Chrome $chrome
    Write-Host "Auto-start at logon: $StartupLink"
} elseif (Test-Path $StartupLink) {
    Remove-Item $StartupLink -Force
}

# 2. Port proxy. It depends on the IP Helper service.
Set-Service -Name iphlpsvc -StartupType Automatic
Start-Service -Name iphlpsvc
Remove-PortProxy
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$ListenPort connectaddress=127.0.0.1 connectport=$ChromePort | Out-Null
Write-Host "Port proxy: 0.0.0.0:$ListenPort -> 127.0.0.1:$ChromePort"

# 3. Firewall: NAS only.
Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP -LocalPort $ListenPort `
    -RemoteAddress $NasIp -Profile $FirewallProfile -Action Allow | Out-Null
Write-Host "Firewall: TCP $ListenPort inbound allowed only from $NasIp ($($FirewallProfile -join ', ') networks)"

# The rule only applies on the profile(s) above; a LAN marked Public would
# silently block the NAS and the agent would just report the desktop offline.
$active = Get-NetConnectionProfile | Where-Object { $_.IPv4Connectivity -ne 'Disconnected' }
foreach ($net in $active) {
    if ($FirewallProfile -notcontains [string]$net.NetworkCategory) {
        Write-Warning ("Network '{0}' ({1}) is {2}, so the firewall rule does not apply there. If this is your home LAN, run:  Set-NetConnectionProfile -InterfaceAlias '{1}' -NetworkCategory Private" -f $net.Name, $net.InterfaceAlias, $net.NetworkCategory)
    }
}

$ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -ExpandProperty IPAddress
Write-Host ''
Write-Host 'Done. Next:'
Write-Host '  1. Open "Agent Chrome (AOC)" from the desktop and log in to your booking sites in that window.'
Write-Host '  2. On the NAS, put this PC''s address in the project root file browser_cdp_url, e.g.:'
foreach ($ip in $ips) { Write-Host "       http://${ip}:$ListenPort" }
Write-Host '     (give this PC a DHCP reservation so the address does not change)'
Write-Host '  3. Check from the NAS:  docker exec -it aoc python scripts/test_browser.py'
