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
$kmWord = "$([char]0x516C)$([char]0x91CC)"

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
    $url = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list?mid=$mid&season_id=$seasonId&sort_reverse=false&page_num=1&page_size=100"
    $response = Invoke-BiliJson $url "https://space.bilibili.com/$mid/lists/$seasonId"
    if ($response.code -ne 0) { throw "Bilibili season API: $($response.message)" }
    return @($response.data.archives | ForEach-Object { Normalize-Archive $_ "season" } | Where-Object { $_ })
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

$parsed = Get-Content -Raw -Encoding UTF8 $dataPath | ConvertFrom-Json
$existing = @()
foreach ($entry in $parsed) { $existing += $entry }
$byBvid = @{}
foreach ($item in $existing) { $byBvid[$item.bvid] = $item }

$archives = Merge-ArchiveSources
if ($archives.Count -eq 0) { throw "No Bilibili video source returned archives." }

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
    $item | Add-Member -NotePropertyName duration -NotePropertyValue $archive.duration -Force
    $item | Add-Member -NotePropertyName views -NotePropertyValue $archive.views -Force
    $item | Add-Member -NotePropertyName cover -NotePropertyValue ($archive.pic -replace "^http:", "https:") -Force
    $item | Add-Member -NotePropertyName videoSource -NotePropertyValue $archive.source -Force
    $item.riskFlags = Get-ReviewFlags $item $previous
    $items += $item
    $previous = $item
}

$items | ConvertTo-Json -Depth 14 | Set-Content -Encoding UTF8 $dataPath
Write-Host "Updated $($items.Count) episodes from Bilibili sources. Latest: $($items[-1].date) $($items[-1].title)"
