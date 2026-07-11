$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dataPath = Join-Path $root "wangshifu-data.json"
$outputPath = Join-Path $root "bilibili-candidates.json"
$headers = @{
    "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
    "Referer" = "https://www.bilibili.com/"
    "Accept" = "application/json, text/plain, */*"
}

$items = @(Get-Content -Raw -Encoding UTF8 $dataPath | ConvertFrom-Json)
$reviewPattern = "??|??|???|pending|auto-added"
$targets = @($items | Sort-Object date)
$contentPattern = "???|??|??|??|??|??|???|???|??|??|??|??|??|??|??|??|??|\bKM\b|??|??|??|??|??|??|Hotel|??|??|??|??|??|??|???|??|??|??|???|??|\d+(?:\.\d+)?\s*(?:?|???|CNY|?????|VND)"
$results = @()
$successfulVideos = 0

foreach ($episode in $targets) {
    try {
        $viewUrl = "https://api.bilibili.com/x/web-interface/view?bvid=$($episode.bvid)"
        $view = Invoke-RestMethod -Uri $viewUrl -Headers $headers -TimeoutSec 25
        if ($view.code -ne 0) { continue }
        $replyUrl = "https://api.bilibili.com/x/v2/reply/main?next=0&type=1&oid=$($view.data.aid)&mode=3&ps=30"
        $reply = Invoke-RestMethod -Uri $replyUrl -Headers $headers -TimeoutSec 25
        if ($reply.code -ne 0) { continue }
        $successfulVideos++
        foreach ($comment in @($reply.data.replies)) {
            $message = [string]$comment.content.message
            if ([int]$comment.like -lt 10 -or $message -notmatch $contentPattern) { continue }
            $results += [pscustomobject]@{
                bvid = $episode.bvid
                date = $episode.date
                place = $episode.place
                user = $comment.member.uname
                likes = [int]$comment.like
                message = $message
                rpid = [string]$comment.rpid
                url = "https://www.bilibili.com/video/$($episode.bvid)/#reply$($comment.rpid)"
                status = "???"
            }
        }
        Start-Sleep -Milliseconds 180
    } catch {
        Write-Warning "Skipped $($episode.bvid): $($_.Exception.Message)"
    }
}

$results = @($results | Sort-Object likes -Descending | Select-Object -First 100)
if ($successfulVideos -gt 0) {
    $newItemsJson = $results | ConvertTo-Json -Depth 8 -Compress
    $oldItemsJson = $null
    if (Test-Path $outputPath) {
        try {
            $oldDocument = Get-Content -Raw -Encoding UTF8 $outputPath | ConvertFrom-Json
            $oldItemsJson = @($oldDocument.items) | ConvertTo-Json -Depth 8 -Compress
        } catch {}
    }
    if ($newItemsJson -eq $oldItemsJson) {
        Write-Host "Scanned $successfulVideos video(s); candidate facts are unchanged."
        return
    }
    [pscustomobject]@{
        generatedAt = [DateTimeOffset]::UtcNow.ToString("o")
        rule = "???????/??/???????? 10 ?????????????????"
        items = $results
    } | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $outputPath
    Write-Host "Scanned $successfulVideos video(s) and collected $($results.Count) public comment candidates."
} else {
    Write-Host "No video comment request succeeded; keeping the previous snapshot."
}

