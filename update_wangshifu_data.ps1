$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dataPath = Join-Path $root "wangshifu-data.json"
$api = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list?mid=3546619609876957&season_id=8168269&sort_reverse=false&page_num=1&page_size=100"
$headers = @{
    "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
    "Referer" = "https://space.bilibili.com/3546619609876957/lists/8168269"
    "Accept" = "application/json, text/plain, */*"
}
$kmWord = "$([char]0x516C)$([char]0x91CC)"

$parsed = Get-Content -Raw -Encoding UTF8 $dataPath | ConvertFrom-Json
$existing = @()
foreach ($entry in $parsed) { $existing += $entry }
$byBvid = @{}
foreach ($item in $existing) { $byBvid[$item.bvid] = $item }

$response = Invoke-RestMethod -Uri $api -Headers $headers -TimeoutSec 30
if ($response.code -ne 0) { throw "Bilibili API: $($response.message)" }

$items = @()
$previous = $null
foreach ($archive in $response.data.archives) {
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
            phase = "Auto-added"
            ride = $true
            distanceKm = $distance
            food = "Not identified"
            foods = @()
            highlights = @()
            evidence = @()
            summary = $archive.title.TrimEnd(".") + "."
        }
    }
    if (-not $item.PSObject.Properties["foods"]) {
        $item | Add-Member -NotePropertyName foods -NotePropertyValue @()
    }
    if (-not $item.PSObject.Properties["highlights"]) {
        $item | Add-Member -NotePropertyName highlights -NotePropertyValue @()
    }
    if (-not $item.PSObject.Properties["evidence"]) {
        $item | Add-Member -NotePropertyName evidence -NotePropertyValue @()
    }
    $item.title = $archive.title
    $item.date = $date
    $item | Add-Member -NotePropertyName duration -NotePropertyValue $archive.duration -Force
    $item | Add-Member -NotePropertyName views -NotePropertyValue $archive.stat.view -Force
    $item | Add-Member -NotePropertyName cover -NotePropertyValue ($archive.pic -replace "^http:", "https:") -Force
    $items += $item
    $previous = $item
}

$items | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 $dataPath
Write-Host "Updated $($items.Count) episodes. Latest: $($items[-1].date) $($items[-1].title)"
