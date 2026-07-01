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
$reviewPattern = "推断|沿用|待核对|pending|auto-added"
$targets = @($items | Sort-Object date)
$contentPattern = "友谊关|友誼關|凭祥|口岸|海关|起点|终点|出发|到达|公里|里程|吃了|早餐|午餐|晚餐|餐馆|酒店|河内|海防|下龙|谅山|宁平|清化|荣市|河静|洞海|顺化|岘港|会安|广义|归仁|绥和|芽庄|大叻|潘切|头顿|胡志明|芹苴"
$results = @()

foreach ($episode in $targets) {
    try {
        $viewUrl = "https://api.bilibili.com/x/web-interface/view?bvid=$($episode.bvid)"
        $view = Invoke-RestMethod -Uri $viewUrl -Headers $headers -TimeoutSec 25
        if ($view.code -ne 0) { continue }
        $replyUrl = "https://api.bilibili.com/x/v2/reply/main?next=0&type=1&oid=$($view.data.aid)&mode=3&ps=30"
        $reply = Invoke-RestMethod -Uri $replyUrl -Headers $headers -TimeoutSec 25
        if ($reply.code -ne 0) { continue }
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
                status = "待核验"
            }
        }
        Start-Sleep -Milliseconds 180
    } catch {
        Write-Warning "Skipped $($episode.bvid): $($_.Exception.Message)"
    }
}

$results = @($results | Sort-Object likes -Descending | Select-Object -First 100)
if ($results.Count -gt 0) {
    [pscustomobject]@{
        generatedAt = [DateTimeOffset]::UtcNow.ToString("o")
        rule = "公开评论含路线/里程/饮食关键词且至少 10 赞；仅作为候选，不自动覆盖正式路线"
        items = $results
    } | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $outputPath
    Write-Host "Collected $($results.Count) public comment candidates."
} else {
    Write-Host "No new candidates; keeping the previous snapshot."
}

