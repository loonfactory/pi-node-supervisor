<#
================================================================================
 CaptureFTP deployment script
================================================================================
 Configures an IIS FTPS upload server on a clean Windows machine.
 The script is idempotent and can be run again to update the deployment.

 Run this script from an elevated 64-bit Windows PowerShell session.

 Examples
   # Deploy with defaults
   .\deploy-captureftp.ps1 -Password 'replace-with-a-strong-password'

   # Or keep the password out of the process command line
   $env:CAPTURE_NODE_FTPS_PASSWORD = 'replace-with-a-strong-password'
   .\deploy-captureftp.ps1

   # Deploy with custom names, paths, and network restrictions
   .\deploy-captureftp.ps1 -Password 'xxx' -SiteName 'NodeB-FTP' `
       -RootPath 'D:\Upload' -FtpUser 'nodeb' -CertIpAddress '192.168.0.10' `
       -AllowedRemoteIp '192.168.0.0/24'

   # Allow plain FTP for a legacy client without TLS support
   .\deploy-captureftp.ps1 -Password 'xxx' -PlainFtp

   # Inspect prerequisites without changing the machine
   .\deploy-captureftp.ps1 -WhatIfOnly
================================================================================
#>
# Disable positional binding so a misspelled parameter cannot be consumed as a
# value and silently shift every subsequent argument.
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $false)]
    [string]$Password,

    [string]$SiteName    = 'CaptureFTP',
    [string]$RootPath    = 'C:\Users\Public\CaptureUpload',
    [string]$FtpUser     = 'captureftp',
    [string]$Description = 'CaptureNode FTP upload account',

    # DNS SAN for the server certificate. Defaults to this computer name.
    [string]$CertDnsName = $env:COMPUTERNAME,

    # IPAddress SAN used when a client connects by IP address.
    [string]$CertIpAddress = '',

    # Fixed passive data-channel range used to keep the firewall narrow.
    [int]$PassiveLow  = 50000,
    [int]$PassiveHigh = 50100,

    # Remote address accepted by the firewall. 'Any' opens it to all peers.
    [string]$AllowedRemoteIp = 'Any',

    # Public address advertised to clients when the server is behind NAT.
    [string]$ExternalIp = '',

    [switch]$PlainFtp,      # Allow TLS instead of requiring it
    [switch]$WriteOnly,     # Allow uploads but block listing and downloads
    [switch]$WhatIfOnly     # Inspect only
)

$ErrorActionPreference = 'Stop'
$appcmd = "$env:windir\system32\inetsrv\appcmd.exe"

function Head($m) { Write-Host ""; Write-Host "=== $m ===" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [OK]   $m" -ForegroundColor Green }
function Skip($m) { Write-Host "  [SKIP] $m" -ForegroundColor DarkGray }
function Warn($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }

function Assert-NativeSuccess($operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$operation failed with exit code $LASTEXITCODE"
    }
}

function New-FtpRequest($uri, $method, $credential) {
    $request = [System.Net.FtpWebRequest]::Create($uri)
    $request.Method = $method
    $request.Credentials = $credential
    $request.EnableSsl = -not $PlainFtp
    $request.UsePassive = $true
    $request.UseBinary = $true
    $request.KeepAlive = $false
    $request.Timeout = 10000
    $request.ReadWriteTimeout = 10000
    return $request
}

function Close-FtpResponse($request) {
    $response = $request.GetResponse()
    try {
        return $response.StatusDescription.Trim()
    } finally {
        $response.Close()
    }
}

# Resolve the built-in group by SID so Windows display language does not matter.
function Get-GroupBySid($sid) {
    Get-LocalGroup | Where-Object { $_.SID.Value -eq $sid }
}

# ---------------------------------------------------------------- 0. Prerequisites
Head "0. Execution environment"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "  Computer       : $env:COMPUTERNAME"
Write-Host "  64-bit process : $([Environment]::Is64BitProcess)"
Write-Host "  Administrator  : $isAdmin"

if (-not $isAdmin) { Bad "Run this script as Administrator."; exit 1 }
if (-not [Environment]::Is64BitProcess) {
    Bad "This is a 32-bit PowerShell process. Run the following executable:"
    Write-Host "       C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    exit 1
}
if (-not $Password -and $env:CAPTURE_NODE_FTPS_PASSWORD) {
    $Password = $env:CAPTURE_NODE_FTPS_PASSWORD
}
if (-not $WhatIfOnly -and -not $Password) {
    Bad "Specify -Password, set CAPTURE_NODE_FTPS_PASSWORD, or use -WhatIfOnly."
    exit 1
}
# Catch common parameter spelling mistakes before values are applied.
if ($Password -like '-*') {
    Bad "The password starts with '-': '$Password'"
    Write-Host "       A parameter name may have been passed as its value. Use one dash:" -ForegroundColor Yellow
    Write-Host "         .\deploy-captureftp.ps1 -Password 'actual-password'" -ForegroundColor Yellow
    exit 1
}
# Reject a shifted argument list that places the password in the site name.
if ($Password -and $SiteName -eq $Password) {
    Bad "SiteName and Password are identical. Check the argument order."
    exit 1
}
if ($CertIpAddress) {
    $parsedIp = $null
    if (-not [System.Net.IPAddress]::TryParse($CertIpAddress, [ref]$parsedIp)) {
        Bad "Invalid -CertIpAddress value: '$CertIpAddress'"
        exit 1
    }
}
if ($PassiveLow -lt 1 -or $PassiveHigh -gt 65535 -or $PassiveLow -gt $PassiveHigh) {
    Bad "Invalid passive port range: $PassiveLow-$PassiveHigh"
    exit 1
}
Ok "Execution environment is valid"

# ---------------------------------------------------------------- 1. IIS FTP features
Head "1. IIS FTP features"
$features = @('IIS-WebServerRole', 'IIS-FTPServer', 'IIS-FTPSvc', 'IIS-ManagementScriptingTools')
$needRestart = $false

foreach ($f in $features) {
    $state = (Get-WindowsOptionalFeature -Online -FeatureName $f -ErrorAction SilentlyContinue).State
    if ($state -eq 'Enabled') {
        Skip "$f is already enabled"
    } elseif ($WhatIfOnly) {
        Warn "$f must be enabled"
    } else {
        Write-Host "  Enabling: $f ..."
        $r = Enable-WindowsOptionalFeature -Online -FeatureName $f -All -NoRestart
        if ($null -eq $r) { throw "Cannot determine the installation result for $f." }
        if ($r.RestartNeeded) { $needRestart = $true }
        Ok "$f enabled"
    }
}
if ($needRestart) { Warn "One or more features require a reboot after deployment." }

if (-not (Test-Path $appcmd)) { Bad "appcmd.exe was not found. Check the IIS installation."; exit 1 }

# ---------------------------------------------------------------- 2. Local account
Head "2. FTP account '$FtpUser'"
$existing = Get-LocalUser -Name $FtpUser -ErrorAction SilentlyContinue

if ($WhatIfOnly) {
    if ($existing) { Skip "Account exists (Enabled=$($existing.Enabled))" } else { Warn "Account must be created" }
} else {
    $sec = ConvertTo-SecureString $Password -AsPlainText -Force
    if ($existing) {
        # Always reset the password so the supplied credential matches Windows.
        Set-LocalUser -Name $FtpUser -Password $sec -PasswordNeverExpires $true
        Enable-LocalUser -Name $FtpUser
        Ok "Existing account password reset and account enabled"
    } else {
        New-LocalUser -Name $FtpUser -Password $sec -Description $Description `
                      -PasswordNeverExpires -AccountNeverExpires | Out-Null
        Ok "Account created"
    }

    # New-LocalUser does not add the account to a local group. Membership in the
    # built-in Users group supplies the logon rights required by IIS FTP.
    $usersGrp = Get-GroupBySid 'S-1-5-32-545'
    $isMember = Get-LocalGroupMember -Group $usersGrp.Name -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like "*\$FtpUser" }
    if ($isMember) {
        Skip "Account already belongs to '$($usersGrp.Name)'"
    } else {
        Add-LocalGroupMember -Group $usersGrp.Name -Member $FtpUser
        Ok "Account added to '$($usersGrp.Name)'"
    }
}

# ---------------------------------------------------------------- 3. Upload directory
Head "3. Upload directory $RootPath"
if (-not (Test-Path $RootPath)) {
    if ($WhatIfOnly) { Warn "Directory must be created" }
    else { New-Item -ItemType Directory -Path $RootPath -Force | Out-Null; Ok "Directory created" }
} else { Skip "Directory exists" }

if (-not $WhatIfOnly) {
    # (OI)(CI) propagates Modify access to child files and directories.
    & icacls $RootPath /grant "${FtpUser}:(OI)(CI)M" /Q | Out-Null
    Assert-NativeSuccess "Granting NTFS access"
    Ok "NTFS Modify access granted"
}

# ---------------------------------------------------------------- 4. Certificate
Head "4. Server certificate"
$cert = $null
if (-not $PlainFtp) {
    $certFriendlyName = "$SiteName FTPS [$CertDnsName][$CertIpAddress]"
    $cert = Get-ChildItem Cert:\LocalMachine\My |
            Where-Object { $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) -and
                           $_.FriendlyName -eq $certFriendlyName } |
            Sort-Object NotAfter -Descending | Select-Object -First 1

    if ($cert) {
        Skip "Reusing certificate: $($cert.Subject) (expires $($cert.NotAfter))"
    } elseif ($WhatIfOnly) {
        Warn "A certificate for '$CertDnsName' must be created (IP SAN: '$CertIpAddress')"
    } else {
        if ($CertIpAddress) {
            $san = "2.5.29.17={text}DNS=$CertDnsName&IPAddress=$CertIpAddress"
            $cert = New-SelfSignedCertificate -Type SSLServerAuthentication `
                        -Subject "CN=$CertDnsName" -TextExtension $san `
                        -CertStoreLocation Cert:\LocalMachine\My `
                        -FriendlyName $certFriendlyName `
                        -NotAfter (Get-Date).AddYears(5)
        } else {
            $cert = New-SelfSignedCertificate -DnsName $CertDnsName `
                        -CertStoreLocation Cert:\LocalMachine\My `
                        -FriendlyName $certFriendlyName `
                        -NotAfter (Get-Date).AddYears(5)
        }
        Ok "Self-signed certificate created: DNS=$CertDnsName, IP=$CertIpAddress (5 years)"

        # Trust the certificate on this server so the local deployment probe can
        # validate it. CaptureNode still requires the exported PEM file.
        $root = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root','LocalMachine')
        $root.Open('ReadWrite'); $root.Add($cert); $root.Close()
        Ok "Certificate added to the local trusted root store"
    }

    if (-not $WhatIfOnly) {
        $pemPath = Join-Path (Split-Path $RootPath -Parent) "$SiteName-cert.pem"
        $base64 = [Convert]::ToBase64String(
            $cert.RawData,
            [System.Base64FormattingOptions]::InsertLineBreaks
        )
        $pem = "-----BEGIN CERTIFICATE-----`n$base64`n-----END CERTIFICATE-----`n"
        [IO.File]::WriteAllText($pemPath, $pem, [Text.UTF8Encoding]::new($false))
        Ok "PEM certificate exported: $pemPath (deploy it as CaptureNode ca_file)"
    }
} else { Skip "Plain FTP mode does not require a certificate" }

# ---------------------------------------------------------------- 5. FTP site
Head "5. FTP site '$SiteName'"
& $appcmd list site "$SiteName" 2>&1 | Out-Null
$siteExists = ($LASTEXITCODE -eq 0)

if ($WhatIfOnly) {
    if ($siteExists) { Skip "Site exists" } else { Warn "Site must be created" }
} else {
    if ($siteExists) {
        & $appcmd set vdir "$SiteName/" /physicalPath:"$RootPath" | Out-Null
        Assert-NativeSuccess "Updating the existing FTP site RootPath"
        Ok "Existing site RootPath updated"
    } else {
        & $appcmd add site /name:"$SiteName" /bindings:"ftp://*:21" /physicalPath:"$RootPath" | Out-Null
        Assert-NativeSuccess "Creating the FTP site"
        Ok "Site created (ftp://*:21 -> $RootPath)"
    }

    # ---- SSL policy ----
    if ($PlainFtp) {
        & $appcmd set site /site.name:"$SiteName" /ftpServer.security.ssl.controlChannelPolicy:"SslAllow" | Out-Null
        Assert-NativeSuccess "Configuring the FTP control-channel SSL policy"
        & $appcmd set site /site.name:"$SiteName" /ftpServer.security.ssl.dataChannelPolicy:"SslAllow"    | Out-Null
        Assert-NativeSuccess "Configuring the FTP data-channel SSL policy"
        Warn "Plain FTP is allowed. Passwords can be exposed on the network."
    } else {
        # Bind the certificate before requiring TLS. Reversing this order leaves
        # the site temporarily unable to accept a TLS login.
        & $appcmd set site /site.name:"$SiteName" /ftpServer.security.ssl.serverCertHash:"$($cert.Thumbprint)" | Out-Null
        Assert-NativeSuccess "Configuring the FTPS certificate thumbprint"
        & $appcmd set site /site.name:"$SiteName" /ftpServer.security.ssl.serverCertStoreName:"MY"             | Out-Null
        Assert-NativeSuccess "Configuring the FTPS certificate store"
        & $appcmd set site /site.name:"$SiteName" /ftpServer.security.ssl.controlChannelPolicy:"SslRequire"    | Out-Null
        Assert-NativeSuccess "Requiring TLS on the FTPS control channel"
        & $appcmd set site /site.name:"$SiteName" /ftpServer.security.ssl.dataChannelPolicy:"SslRequire"       | Out-Null
        Assert-NativeSuccess "Requiring TLS on the FTPS data channel"
        Ok "FTPS required and certificate bound ($($cert.Thumbprint))"
    }

    # ---- Authentication ----
    # Site authentication belongs to the site object's ftpServer.security path,
    # not the server-wide authentication section. Without a provider, IIS returns
    # 530 even when the Windows credential is valid.
    & $appcmd set site /site.name:"$SiteName" `
        /ftpServer.security.authentication.basicAuthentication.enabled:"True" 2>&1 | Out-Null
    Assert-NativeSuccess "Enabling FTP basic authentication"
    & $appcmd set site /site.name:"$SiteName" `
        /ftpServer.security.authentication.anonymousAuthentication.enabled:"False" 2>&1 | Out-Null
    Assert-NativeSuccess "Disabling FTP anonymous authentication"

    $basicNow = (& $appcmd list site "$SiteName" `
        /text:ftpServer.security.authentication.basicAuthentication.enabled) -join ''
    Assert-NativeSuccess "Reading the FTP basic authentication state"

    if ($basicNow -eq 'true') {
        Ok "Basic authentication is enabled and anonymous access is disabled"
    } else {
        Bad "Basic authentication could not be enabled. No account can log in."
        Write-Host "  Observed value: '$basicNow'" -ForegroundColor Yellow
        & $appcmd list site "$SiteName" /config
        exit 1
    }

    # ---- Authorization ----
    $perm = if ($WriteOnly) { 'Write' } else { 'Read,Write' }
    # Replace all existing rules to keep repeated deployments deterministic.
    & $appcmd clear config "$SiteName" /section:system.ftpServer/security/authorization /commit:apphost 2>&1 | Out-Null
    Assert-NativeSuccess "Removing existing FTP authorization rules"
    & $appcmd set config "$SiteName" /section:system.ftpServer/security/authorization `
        /+"[accessType='Allow',users='$FtpUser',permissions='$perm']" /commit:apphost | Out-Null
    Assert-NativeSuccess "Configuring the FTP authorization rule"
    Ok "Authorization rule: $FtpUser = $perm"

    # ---- Passive port range ----
    & $appcmd set config /section:system.ftpServer/firewallSupport `
        /lowDataChannelPort:"$PassiveLow" /highDataChannelPort:"$PassiveHigh" | Out-Null
    Assert-NativeSuccess "Configuring the FTP passive port range"
    Ok "Passive port range: $PassiveLow-$PassiveHigh"

    if ($ExternalIp) {
        & $appcmd set site /site.name:"$SiteName" /ftpServer.firewallSupport.externalIp4Address:"$ExternalIp" | Out-Null
        Assert-NativeSuccess "Configuring the FTP NAT external address"
        Ok "NAT external address: $ExternalIp"
    }
}

# ---------------------------------------------------------------- 6. Firewall
Head "6. Firewall"
# Create rules with stable names because built-in FTP rule display names depend
# on the Windows display language.
$rules = @(
    @{ Name = "$SiteName-Control"; Port = "21";                          Desc = 'FTP control channel' },
    @{ Name = "$SiteName-Passive"; Port = "$PassiveLow-$PassiveHigh";    Desc = 'FTP passive data channel' }
)

foreach ($r in $rules) {
    if ($WhatIfOnly) { Warn "Rule '$($r.Name)' must be checked or created"; continue }
    $existingRule = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
    if ($existingRule) { Remove-NetFirewallRule -DisplayName $r.Name }
    New-NetFirewallRule -DisplayName $r.Name -Description $r.Desc `
        -Direction Inbound -Protocol TCP -LocalPort $r.Port `
        -RemoteAddress $AllowedRemoteIp -Action Allow -Enabled True -Profile Any | Out-Null
    Ok "$($r.Name)  port $($r.Port)  remote $AllowedRemoteIp"
}

if ($AllowedRemoteIp -eq 'Any') {
    Warn "RemoteAddress is Any. Restrict it with -AllowedRemoteIp for production."
}

# ---------------------------------------------------------------- 7. Service
Head "7. Service"
if ($WhatIfOnly) {
    Skip "ftpsvc status: $((Get-Service ftpsvc -ErrorAction SilentlyContinue).Status)"
} else {
    Set-Service -Name ftpsvc -StartupType Automatic
    Restart-Service ftpsvc -Force
    Start-Sleep -Seconds 3
    Ok "ftpsvc restarted with Automatic startup"
}

# ---------------------------------------------------------------- 8. Validation
if ($WhatIfOnly) { Head "Inspection complete; no changes were made"; exit 0 }

Head "8. Validation"
$clientHost = if ($CertIpAddress) { $CertIpAddress } else { $CertDnsName }
$credential = New-Object System.Net.NetworkCredential($FtpUser, $Password)
$probeName = ".captureftp-deploy-$([Guid]::NewGuid().ToString('N')).tmp"
$probeUri = [UriBuilder]::new('ftp', $clientHost, 21, $probeName).Uri
$rootUri = [UriBuilder]::new('ftp', $clientHost, 21, '/').Uri
$probeBytes = [Text.Encoding]::UTF8.GetBytes("CaptureFTP deployment probe`n")
$probeUploaded = $false
$probeDeleted = $false
$validationFailed = $false

try {
    $upload = New-FtpRequest $probeUri ([Net.WebRequestMethods+Ftp]::UploadFile) $credential
    $upload.ContentLength = $probeBytes.Length
    $uploadStream = $upload.GetRequestStream()
    try {
        $uploadStream.Write($probeBytes, 0, $probeBytes.Length)
    } finally {
        $uploadStream.Close()
    }
    $uploadStatus = Close-FtpResponse $upload
    $probeUploaded = $true
    Ok "Upload succeeded -- $uploadStatus"

    if ($WriteOnly) {
        Warn "WriteOnly mode skips listing and download validation."
    } else {
        $listingRequest = New-FtpRequest $rootUri ([Net.WebRequestMethods+Ftp]::ListDirectory) $credential
        $listingResponse = $listingRequest.GetResponse()
        try {
            $listingReader = New-Object IO.StreamReader($listingResponse.GetResponseStream())
            try {
                $listing = $listingReader.ReadToEnd()
            } finally {
                $listingReader.Close()
            }
        } finally {
            $listingResponse.Close()
        }
        if (($listing -split '\r?\n') -notcontains $probeName) {
            throw "The uploaded probe file is missing from the directory listing."
        }
        Ok "Passive directory listing succeeded"

        $download = New-FtpRequest $probeUri ([Net.WebRequestMethods+Ftp]::DownloadFile) $credential
        $downloadResponse = $download.GetResponse()
        try {
            $downloadStream = $downloadResponse.GetResponseStream()
            $memory = New-Object IO.MemoryStream
            try {
                $downloadStream.CopyTo($memory)
                $downloaded = $memory.ToArray()
            } finally {
                $memory.Close()
                $downloadStream.Close()
            }
        } finally {
            $downloadResponse.Close()
        }
        if ([Convert]::ToBase64String($probeBytes) -ne
            [Convert]::ToBase64String($downloaded)) {
            throw "The downloaded probe content does not match the upload."
        }
        Ok "Passive download and content verification succeeded"
    }

    $delete = New-FtpRequest $probeUri ([Net.WebRequestMethods+Ftp]::DeleteFile) $credential
    $deleteStatus = Close-FtpResponse $delete
    $probeDeleted = $true
    Ok "Probe deletion succeeded -- $deleteStatus"

    if ($PlainFtp) {
        Ok "Plain FTP upload validation completed"
    } else {
        Ok "Certificate and explicit FTPS control/data channel validation completed"
    }
} catch {
    Bad "Validation failed: $($_.Exception.Message)"
    $validationFailed = $true
} finally {
    if ($probeUploaded -and -not $probeDeleted) {
        try {
            $cleanup = New-FtpRequest $probeUri ([Net.WebRequestMethods+Ftp]::DeleteFile) $credential
            Close-FtpResponse $cleanup | Out-Null
        } catch {
            Warn "Automatic probe cleanup failed: $probeName"
        }
    }
}
if ($validationFailed) { exit 1 }

Head "Deployment complete"
Write-Host "  Host       : $clientHost"
Write-Host "  Port       : 21 / passive $PassiveLow-$PassiveHigh"
Write-Host "  User       : $FtpUser"
Write-Host "  Root path  : $RootPath"
Write-Host "  Encryption : $(if ($PlainFtp) { 'Plain FTP' } else { 'Explicit FTPS required' })"
Write-Host ""
if (-not $PlainFtp) {
    Write-Host "  CaptureNode ca_file: $pemPath" -ForegroundColor Cyan
}
