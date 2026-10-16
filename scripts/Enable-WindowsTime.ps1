param([Parameter(Mandatory = $true)][string]$ResultPath)

$ErrorActionPreference = 'Stop'
$record = @{ Configured = $false; Message = ''; ResyncRequested = $false }
try {
    $service = Get-CimInstance Win32_Service -Filter "Name='W32Time'"
    $parametersPath = 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters'
    $clientPath = 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient'
    $parameters = Get-ItemProperty -LiteralPath $parametersPath
    $client = Get-ItemProperty -LiteralPath $clientPath
    $before = @{ StartMode = $service.StartMode; State = $service.State; Type = $parameters.Type;
                 NtpServer = $parameters.NtpServer; NtpClientEnabled = $client.Enabled }
    $backupPath = [IO.Path]::Combine([IO.Path]::GetDirectoryName($ResultPath), 'windows-time-before.json')
    if (-not (Test-Path -LiteralPath $backupPath)) {
        [IO.File]::WriteAllText($backupPath, ($before | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    }
    Set-Service -Name W32Time -StartupType Automatic
    Set-ItemProperty -LiteralPath $clientPath -Name Enabled -Value 1
    Start-Service -Name W32Time
    (Get-Service -Name W32Time).WaitForStatus('Running', [TimeSpan]::FromSeconds(15))
    $domainJoined = (Get-CimInstance Win32_ComputerSystem).PartOfDomain
    if (-not $domainJoined -and $parameters.Type -in @('NoSync', 'NTP')) {
        $peer = $parameters.NtpServer
        if ([string]::IsNullOrWhiteSpace($peer)) { $peer = 'time.windows.com,0x8' }
        # Keep server addresses; replace the default special-interval flag with adaptive polling.
        $adaptivePeer = [regex]::Replace($peer, ',0x9(?=\s|$)', ',0x8', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($parameters.Type -eq 'NoSync' -or $adaptivePeer -ne $parameters.NtpServer) {
            & "$env:SystemRoot\System32\w32tm.exe" /config "/manualpeerlist:$adaptivePeer" /syncfromflags:manual /update | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Could not configure the Windows time source.' }
        }
    }
    & "$env:SystemRoot\System32\w32tm.exe" /resync /rediscover | Out-Null
    $record.ResyncRequested = ($LASTEXITCODE -eq 0)
    $record.Configured = ((Get-Service -Name W32Time).Status -eq 'Running')
    $record.Message = if ($record.ResyncRequested) { 'Windows Time service enabled; synchronization requested.' } else { 'Service enabled; synchronization request failed. Check the configured source and network.' }
} catch {
    $record.Message = $_.Exception.Message
}
$record.CompletedUtc = [DateTime]::UtcNow.ToString('o')
[IO.File]::WriteAllText($ResultPath, ($record | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
if ($record.Configured -and $record.ResyncRequested) { exit 0 } else { exit 1 }
