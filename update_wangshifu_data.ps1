$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dataPath = Join-Path $root "wangshifu-data.json"

$mid = "3546619609876957"
$seasonId = "8168269"
$headers = @{
    "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
    "Referer" = "https://space.bilibili.com/$mid"
    "Accept" = "application/json, text/plain, */*"
}

function Join-Chars([int[]]$codes) {
    $chars = foreach ($code in $codes) { [char]$code }
    return -join $chars
}

$kmWord = Join-Chars @(0x516C, 0x91CC)

function Convert-DurationToSeconds($value) {
    if ($null -eq $value) { return $null }
    if ($value -is [int] -or $value -is [long] -or $value -is [double]) { return [int]$value }
    $text = [string]$value
    if ($text -match "^\d+$") { return [int]$text }
    $parts = @($text -split ":" | ForEach-Object { [int]$_ })
    if ($parts.Count -eq 2) { return $parts[0] * 60 + $parts[1] }
    if ($parts.Count -eq 3) { return $parts[0] * 3600 + $parts[1] * 60 + $parts[2] }
    return $null
}

function Normalize-Archive($archive, [string]$source) {
    $bvid = $archive.bvid
    if ($null -eq $bvid) { $bvid = $archive.bv_id }
    $bvid = [string]$bvid
    if ([string]::IsNullOrWhiteSpace($bvid)) { return $null }

    $pubdate = $archive.pubdate
    if ($null -eq $pubdate) { $pubdate = $archive.created }
    if ($null -eq $pubdate) { $pubdate = $archive.ctime }
    if ($null -eq $pubdate) { return $null }

    $stat = $archive.stat
    $view = $null
    if ($null -ne $stat) { $view = $stat.view }
    if ($null -eq $view) { $view = $archive.play }

    [pscustomobject]@{
        bvid = $bvid
        title = [string]$archive.title
        pubdate = [long]$pubdate
        duration = Convert-DurationToSeconds $(if ($null -ne $archive.duration) { $archive.duration } else { $archive.length })
        views = $view
        pic = [string]$(if ($null -ne $archive.pic) { $archive.pic } else { $archive.cover })
        source = $source
    }
}

function Invoke-BiliJson([string]$url, [string]$referer) {
    $localHeaders = $headers.Clone()
    $localHeaders["Referer"] = $referer
    return Invoke-RestMethod -Uri $url -Headers $localHeaders -TimeoutSec 30
}

function Fetch-SeasonArchives {
    $pageSize = 100
    $all = @()
    for ($page = 1; $page -le 20; $page++) {
        $url = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list?mid=$mid&season_id=$seasonId&sort_reverse=false&page_num=$page&page_size=$pageSize"
        $response = Invoke-BiliJson $url "https://space.bilibili.com/$mid/lists/$seasonId"
        if ($response.code -ne 0) { throw "Bilibili season API: $($response.message)" }
        $pageItems = @($response.data.archives)
        $all += @($pageItems | ForEach-Object { Normalize-Archive $_ "season" } | Where-Object { $_ })
        if ($pageItems.Count -lt $pageSize) { break }
    }
    return @($all)
}

function Fetch-UploadArchives {
    $urls = @(
        "https://api.bilibili.com/x/space/wbi/arc/search?mid=$mid&ps=30&pn=1&order=pubdate&order_avoided=true&platform=web",
        "https://api.bilibili.com/x/space/arc/search?mid=$mid&ps=30&pn=1&order=pubdate"
    )
    foreach ($url in $urls) {
        try {
            $response = Invoke-BiliJson $url "https://space.bilibili.com/$mid/video"
            if ($response.code -ne 0) { throw "Bilibili upload API: $($response.message)" }
            $list = @()
            if ($response.data.list.vlist) { $list = @($response.data.list.vlist) }
            elseif ($response.data.archives) { $list = @($response.data.archives) }
            if ($list.Count -gt 0) {
                return @($list | ForEach-Object { Normalize-Archive $_ "upload" } | Where-Object { $_ })
            }
        } catch {
            Write-Warning "Upload source failed: $($_.Exception.Message)"
        }
    }
    return @()
}

function Merge-ArchiveSources {
    $byBvid = @{}
    $sources = @()
    try { $sources += Fetch-SeasonArchives } catch { Write-Warning "Season source failed: $($_.Exception.Message)" }
    try { $sources += Fetch-UploadArchives } catch { Write-Warning "Upload source failed: $($_.Exception.Message)" }
    foreach ($archive in $sources) {
        if (-not $archive.bvid) { continue }
        if (-not $byBvid.ContainsKey($archive.bvid)) {
            $byBvid[$archive.bvid] = $archive
        } elseif ($byBvid[$archive.bvid].source -ne "season" -and $archive.source -eq "season") {
            $byBvid[$archive.bvid] = $archive
        }
    }
    return @($byBvid.Values | Sort-Object pubdate)
}

function Get-ReviewFlags($item, $previous) {
    $flags = @()
    if ([string]$item.confidence -match "pending|auto-added") { $flags += "place-needs-auto-review" }
    if ($item.ride -and $null -eq $item.distanceKm) { $flags += "missing-distance" }
    if ($item.ride -and $previous -and $item.lat -eq $previous.lat -and $item.lng -eq $previous.lng) { $flags += "coordinates-copied-from-previous-ride" }
    if (-not $item.highlights -or $item.highlights.Count -eq 0) { $flags += "missing-key-timepoints" }
    return @($flags | Select-Object -Unique)
}

function Test-ManualVerified($item) {
    if ($item.manualVerified -eq $true) { return $true }
    $accepted = Join-Chars @(0x7EF4, 0x62A4, 0x8005, 0x91C7, 0x7EB3)
    $manual = Join-Chars @(0x4EBA, 0x5DE5, 0x6838, 0x9A8C)
    $videoConfirmed = Join-Chars @(0x89C6, 0x9891, 0x786E, 0x8BA4)
    if ([string]$item.confidence -match "$accepted|$manual|$videoConfirmed") { return $true }
    foreach ($evidence in @($item.evidence)) {
        if ($evidence.acceptedBy) { return $true }
    }
    return $false
}

function Resolve-GeoFromText([string]$text) {
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }

    $heDuoA = Join-Chars @(0x6CB3, 0x591A)
    $heDuoB = Join-Chars @(0x548C, 0x591A)
    $jinLan = Join-Chars @(0x91D1, 0x5170)
    $yaZhuang = Join-Chars @(0x82BD, 0x5E84)
    $suiHe = Join-Chars @(0x7EE5, 0x548C)
    $guiRen = Join-Chars @(0x5F52, 0x4EC1)
    $huiAn = Join-Chars @(0x4F1A, 0x5B89)
    $xianGang = Join-Chars @(0x5C98, 0x6E2F)
    $shunHua = Join-Chars @(0x987A, 0x5316)
    $heNei = Join-Chars @(0x6CB3, 0x5185)
    $meiNai = Join-Chars @(0x7F8E, 0x5948)
    $panQie = Join-Chars @(0x6F58, 0x5207)
    $huZhiMing = Join-Chars @(0x80E1, 0x5FD7, 0x660E)
    $xiGong = Join-Chars @(0x897F, 0x8D21)
    $youYiGuan = Join-Chars @(0x53CB, 0x8C0A, 0x5173)
    $youYiGuanTrad = Join-Chars @(0x53CB, 0x8ABC, 0x95DC)
    $pingXiang = Join-Chars @(0x51ED, 0x7965)
    $youYiGuanKouAn = Join-Chars @(0x53CB, 0x8C0A, 0x5173, 0x53E3, 0x5CB8)

    $rules = @(
        [pscustomobject]@{ Pattern = "$heDuoA|$heDuoB|Hoa Da"; Lat = 11.18; Lng = 108.72; Display = "$heDuoB Hoa Da (Binh Thuan)" },
        [pscustomobject]@{ Pattern = "$jinLan|Cam Ranh|Cam Lam"; Lat = 11.9020; Lng = 109.2200; Display = "$jinLan Cam Ranh" },
        [pscustomobject]@{ Pattern = "$yaZhuang|Nha Trang"; Lat = 12.2388; Lng = 109.1967; Display = "$yaZhuang (Nha Trang)" },
        [pscustomobject]@{ Pattern = "$suiHe|Tuy Hoa"; Lat = 13.0955; Lng = 109.3209; Display = "$suiHe (Tuy Hoa)" },
        [pscustomobject]@{ Pattern = "$meiNai|Mui Ne|Mui Ne"; Lat = 10.9330; Lng = 108.2870; Display = "$meiNai Mui Ne" },
        [pscustomobject]@{ Pattern = "$panQie|Phan Thiet"; Lat = 10.9289; Lng = 108.1020; Display = "$panQie Phan Thiet" },
        [pscustomobject]@{ Pattern = "$huZhiMing|$xiGong|Ho Chi Minh|Saigon"; Lat = 10.8231; Lng = 106.6297; Display = "$huZhiMing Ho Chi Minh City" },
        [pscustomobject]@{ Pattern = "$guiRen|Quy Nhon"; Lat = 13.7820; Lng = 109.2190; Display = "$guiRen (Quy Nhon)" },
        [pscustomobject]@{ Pattern = "$huiAn|Hoi An"; Lat = 15.8801; Lng = 108.3380; Display = "$huiAn Hoi An" },
        [pscustomobject]@{ Pattern = "$xianGang|Da Nang"; Lat = 16.0471; Lng = 108.2068; Display = "$xianGang Da Nang" },
        [pscustomobject]@{ Pattern = "$shunHua|Hue"; Lat = 16.4637; Lng = 107.5909; Display = "$shunHua Hue" },
        [pscustomobject]@{ Pattern = "$heNei|Ha Noi|Hanoi"; Lat = 21.0278; Lng = 105.8342; Display = "$heNei Ha Noi" },
        [pscustomobject]@{ Pattern = "$youYiGuan|$youYiGuanTrad|$pingXiang|Huu Nghi"; Lat = 21.97635; Lng = 106.71212; Display = "$youYiGuanKouAn (China-Vietnam border)" }
    )
    foreach ($rule in $rules) {
        if ($text -match $rule.Pattern) { return $rule }
    }
    return $null
}

function Resolve-TitleDestination([string]$title) {
    if ([string]::IsNullOrWhiteSpace($title)) { return $null }
    $distance = Join-Chars @(0x8DDD, 0x79BB)
    $awayFrom = Join-Chars @(0x79BB)
    $remaining = Join-Chars @(0x8FD8, 0x6709)
    $only = Join-Chars @(0x53EA, 0x6709)
    $from = Join-Chars @(0x4ECE)
    $ride = Join-Chars @(0x9A91, 0x884C)
    $depart = Join-Chars @(0x51FA, 0x53D1)
    $cleanTitle = $title -replace "($distance|$awayFrom).{0,24}($remaining|$only).{0,12}", " "
    $cleanTitle = $cleanTitle -replace "$from.{0,24}($ride|$depart)", " "
    return Resolve-GeoFromText $cleanTitle
}

function Resolve-ExcludedDestination([string]$title) {
    if ([string]::IsNullOrWhiteSpace($title)) { return $null }
    $distance = Join-Chars @(0x8DDD, 0x79BB)
    $awayFrom = Join-Chars @(0x79BB)
    $remaining = Join-Chars @(0x8FD8, 0x6709)
    $only = Join-Chars @(0x53EA, 0x6709)
    if ($title -match "($distance|$awayFrom)(.{0,24})($remaining|$only)") {
        return Resolve-GeoFromText $Matches[2]
    }
    return $null
}

function Apply-GeoHint($item, $archive) {
    if (Test-ManualVerified $item) { return }
    $geo = Resolve-TitleDestination ([string]$archive.title)
    $excluded = Resolve-ExcludedDestination ([string]$archive.title)
    if ($null -eq $geo) {
        $placeGeo = Resolve-GeoFromText ([string]$item.place)
        if ($null -eq $excluded -or $null -eq $placeGeo -or $excluded.Display -ne $placeGeo.Display) {
            $geo = $placeGeo
        }
    }
    if ($null -eq $geo) { return }

    $flags = @($item.riskFlags)
    $shouldUpdate = $false
    if ($null -eq $item.lat -or $null -eq $item.lng) { $shouldUpdate = $true }
    if ($flags -contains "coordinates-copied-from-previous-ride") { $shouldUpdate = $true }
    if ($flags -contains "place-copied-from-previous") { $shouldUpdate = $true }
    if ([string]$item.confidence -match "pending|auto-added") { $shouldUpdate = $true }
    if ($null -ne $item.lat -and $null -ne $item.lng) {
        $latDiff = [Math]::Abs(([double]$item.lat) - ([double]$geo.Lat))
        $lngDiff = [Math]::Abs(([double]$item.lng) - ([double]$geo.Lng))
        if ($latDiff -gt 0.15 -or $lngDiff -gt 0.15) { $shouldUpdate = $true }
    }

    if ($shouldUpdate) {
        $item.lat = [double]$geo.Lat
        $item.lng = [double]$geo.Lng
        $item.place = [string]$geo.Display
        $item | Add-Member -NotePropertyName coordinateSource -NotePropertyValue "title/place gazetteer" -Force
        if (-not $item.confidenceScore -or [double]$item.confidenceScore -lt 0.45) {
            $item.confidenceScore = 0.45
        }
    }
}

function Get-HaversineKm($a, $b) {
    $earth = 6371.0
    $toRad = [Math]::PI / 180.0
    $dLat = ([double]$b.lat - [double]$a.lat) * $toRad
    $dLng = ([double]$b.lng - [double]$a.lng) * $toRad
    $value = [Math]::Sin($dLat / 2) * [Math]::Sin($dLat / 2) +
        [Math]::Cos([double]$a.lat * $toRad) * [Math]::Cos([double]$b.lat * $toRad) *
        [Math]::Sin($dLng / 2) * [Math]::Sin($dLng / 2)
    return 2 * $earth * [Math]::Atan2([Math]::Sqrt($value), [Math]::Sqrt(1 - $value))
}

function Apply-RouteSafety([object[]]$routeItems) {
    $previousByPhase = @{}
    for ($index = 0; $index -lt $routeItems.Count; $index++) {
        $item = $routeItems[$index]
        if (-not $item.ride -or $null -eq $item.lat -or $null -eq $item.lng) { continue }
        $phase = [string]$item.phase
        if (Test-ManualVerified $item) {
            $item | Add-Member -NotePropertyName mapVisible -NotePropertyValue $true -Force
            $previousByPhase[$phase] = $item
            continue
        }
        $previous = $previousByPhase[$phase]
        $flags = @($item.riskFlags)
        $conflict = $false
        if ($previous -and $null -ne $item.distanceKm -and [double]$item.distanceKm -gt 0) {
            $straight = Get-HaversineKm $previous $item
            $reported = [double]$item.distanceKm
            if (($straight -lt 1 -and $reported -gt 20) -or
                ($straight -gt [Math]::Max($reported * 2.2, $reported + 80))) {
                $conflict = $true
                $flags += "coordinate-distance-conflict"
                if ($index + 1 -lt $routeItems.Count) {
                    $next = $routeItems[$index + 1]
                    if ($null -ne $next.lat -and $null -ne $next.lng -and
                        [string]$next.phase -eq $phase) {
                        $candidateDistance = Get-HaversineKm $previous $next
                        if ($candidateDistance -ge $reported * 0.3 -and
                            $candidateDistance -le $reported * 1.5) {
                            $item.lat = [double]$next.lat
                            $item.lng = [double]$next.lng
                            $item.place = [string]$next.place
                            $item | Add-Member -NotePropertyName coordinateSource -NotePropertyValue "next-day route context" -Force
                            $flags = @($flags | Where-Object { $_ -ne "coordinate-distance-conflict" })
                            $flags += "coordinate-corrected-from-next-day"
                            $conflict = $false
                        }
                    }
                }
            }
        }
        $item.riskFlags = @($flags | Select-Object -Unique)
        $item | Add-Member -NotePropertyName mapVisible -NotePropertyValue (-not $conflict) -Force
        if ($conflict) {
            $item | Add-Member -NotePropertyName coordinateStatus -NotePropertyValue "distance conflict; hidden from route" -Force
        } else {
            $previousByPhase[$phase] = $item
        }
    }
}

$parsed = Get-Content -Raw -Encoding UTF8 $dataPath | ConvertFrom-Json
$existing = @()
foreach ($entry in $parsed) { $existing += $entry }
$byBvid = @{}
foreach ($item in $existing) { $byBvid[$item.bvid] = $item }

$archives = Merge-ArchiveSources
if ($archives.Count -eq 0) { throw "No Bilibili video source returned archives." }

$archiveIds = @{}
foreach ($archive in $archives) { $archiveIds[$archive.bvid] = $true }
foreach ($cached in $existing) {
    if (-not $cached.bvid -or $archiveIds.ContainsKey([string]$cached.bvid) -or -not $cached.date) { continue }
    $archives += [pscustomobject]@{
        bvid = [string]$cached.bvid
        title = [string]$cached.title
        pubdate = [DateTimeOffset]::Parse("$($cached.date)T12:00:00+08:00").ToUnixTimeSeconds()
        duration = $cached.duration
        views = $cached.views
        pic = [string]$cached.cover
        source = "existing-cache"
    }
}
$archives = @($archives | Sort-Object pubdate)

$items = @()
$previous = $null
foreach ($archive in $archives) {
    $date = [DateTimeOffset]::FromUnixTimeSeconds([long]$archive.pubdate).ToOffset([TimeSpan]::FromHours(8)).ToString("yyyy-MM-dd")
    if ($byBvid.ContainsKey($archive.bvid)) {
        $item = $byBvid[$archive.bvid]
    } else {
        $distance = $null
        if ($archive.title -match "(?<!\d)(\d{1,3})\s*$kmWord") { $distance = [double]$Matches[1] }
        $item = [pscustomobject]@{
            date = $date
            bvid = $archive.bvid
            title = $archive.title
            place = if ($previous) { $previous.place } else { "Location pending review" }
            lat = if ($previous) { $previous.lat } else { $null }
            lng = if ($previous) { $previous.lng } else { $null }
            confidence = "Auto-added; pending review"
            confidenceScore = 0.25
            riskFlags = @("new-video-auto-added", "place-copied-from-previous")
            phase = if ($previous -and $previous.phase) { $previous.phase } else { "Auto-added" }
            ride = $true
            distanceKm = $distance
            food = "Not identified"
            foods = @()
            foodDetails = @()
            lodgings = @()
            costs = @()
            highlights = @()
            evidence = @()
            rideTimeHours = $null
            dayTimeHours = $null
            summary = $archive.title.TrimEnd(".") + "."
        }
    }

    foreach ($name in @("foods","highlights","foodDetails","lodgings","costs","evidence","riskFlags")) {
        if (-not $item.PSObject.Properties[$name]) {
            $item | Add-Member -NotePropertyName $name -NotePropertyValue @()
        }
    }
    foreach ($name in @("rideTimeHours","dayTimeHours")) {
        if (-not $item.PSObject.Properties[$name]) {
            $item | Add-Member -NotePropertyName $name -NotePropertyValue $null
        }
    }
    if (-not $item.PSObject.Properties["confidenceScore"]) {
        $item | Add-Member -NotePropertyName confidenceScore -NotePropertyValue $null
    }
    if (-not $item.PSObject.Properties["automation"]) {
        $item | Add-Member -NotePropertyName automation -NotePropertyValue ([pscustomobject]@{})
    }

    $item.title = $archive.title
    $item.date = $date
    if ([string]$item.phase -eq "Auto-added" -and $previous -and $previous.phase) {
        $item.phase = $previous.phase
    }
    if (-not $item.PSObject.Properties["duration"] -or $null -eq $item.duration) {
        $item | Add-Member -NotePropertyName duration -NotePropertyValue $archive.duration -Force
    }
    if (-not $item.PSObject.Properties["views"] -or $null -eq $item.views) {
        $item | Add-Member -NotePropertyName views -NotePropertyValue $archive.views -Force
    }
    if (-not $item.PSObject.Properties["cover"] -or [string]::IsNullOrWhiteSpace([string]$item.cover)) {
        $item | Add-Member -NotePropertyName cover -NotePropertyValue ($archive.pic -replace "^http:", "https:") -Force
    }
    $item | Add-Member -NotePropertyName videoSource -NotePropertyValue $archive.source -Force
    Apply-GeoHint $item $archive
    $item.riskFlags = @(Get-ReviewFlags $item $previous)
    $items += $item
    $previous = $item
}

Apply-RouteSafety $items

$items | ConvertTo-Json -Depth 14 | Set-Content -Encoding UTF8 $dataPath
Write-Host "Updated $($items.Count) episodes from Bilibili sources. Latest: $($items[-1].date) $($items[-1].title)"
