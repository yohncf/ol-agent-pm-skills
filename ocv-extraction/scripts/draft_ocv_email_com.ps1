# draft_ocv_email_com.ps1 — Outlook COM helper for ocv-draft-email
#
# Actions:
#   create   -Subject <str> -HtmlPath <path> [-ChartImage <png> [-ChartCid <id>]]
#   verify   -EntryID <str>
#   append   -EntryID <str> -HtmlSnippetPath <path>
#
# Emits one machine-readable line per invocation:
#   RESULT_JSON:{...}
#
# When -ChartImage is supplied to `create`, the helper:
#   1. Reads the HTML file
#   2. Rewrites every <img src="ocv_progress_chart*.png"> reference to
#      <img src="cid:<ChartCid>"> so the chart resolves via Content-ID
#   3. Sets HTMLBody + Save() (lands the draft)
#   4. Adds the PNG as an inline MAPI attachment with PR_ATTACH_CONTENT_ID
#      (0x3712001F) = <ChartCid> and PR_ATTACHMENT_HIDDEN (0x7FFE000B) = TRUE
#      so it renders inline in every client (including OWA) and does NOT
#      appear in the recipient's attachment list.
#   5. Save() again so the attachment is persisted on Exchange.
#
# OWA strips inline <svg> on its sanitization round-trip (confirmed
# 2026-06-08), which is why we attach a PNG via CID instead of embedding
# the chart as inline SVG. See SKILL.md "Outlook-safe HTML rules" #4.
#
# Requires CLASSIC Outlook (OUTLOOK.EXE) -- New Outlook (olk.exe) does not
# expose COM. If Outlook is not running, the first COM call will start it.

[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][ValidateSet("create","verify","append")][string]$Action,
  [string]$Subject,
  [string]$HtmlPath,
  [string]$EntryID,
  [string]$HtmlSnippetPath,
  [string]$ChartImage,
  [string]$ChartCid = "ocv_progress_chart"
)

$ErrorActionPreference = "Stop"

function Write-ResultJson($obj) {
    $json = $obj | ConvertTo-Json -Compress -Depth 5
    Write-Host "RESULT_JSON:$json"
}

function Get-Outlook {
    try {
        return [Runtime.InteropServices.Marshal]::GetActiveObject("Outlook.Application")
    } catch {
        return New-Object -ComObject Outlook.Application
    }
}

$ol = Get-Outlook
$ns = $ol.GetNamespace("MAPI")

switch ($Action) {
    "create" {
        if (-not $Subject)  { throw "create requires -Subject"  }
        if (-not $HtmlPath) { throw "create requires -HtmlPath" }
        if (-not (Test-Path $HtmlPath)) { throw "HtmlPath not found: $HtmlPath" }

        $html = [System.IO.File]::ReadAllText($HtmlPath, [System.Text.Encoding]::UTF8)

        # If a chart PNG was supplied, rewrite the preview-time relative
        # <img src="..png"> to <img src="cid:..."> before saving.
        $chartRewritten = $false
        if ($ChartImage) {
            if (-not (Test-Path $ChartImage)) { throw "ChartImage not found: $ChartImage" }
            $pngName = [IO.Path]::GetFileName($ChartImage)
            $regex = 'src=("|'')[^"'']*' + [Regex]::Escape($pngName) + '\1'
            $replacement = 'src="cid:' + $ChartCid + '"'
            $newHtml = [Regex]::Replace($html, $regex, $replacement)
            if ($newHtml -ne $html) {
                $html = $newHtml
                $chartRewritten = $true
            }
        }

        # 0 = olMailItem
        $mail = $ol.CreateItem(0)
        $mail.Subject    = $Subject
        $mail.BodyFormat = 2          # 2 = olFormatHTML
        $mail.HTMLBody   = $html
        $mail.Save()                  # lands in Drafts; To/Cc/Bcc left blank

        # Attach the chart PNG as a hidden inline (CID) attachment.
        $chartAttached = $false
        if ($ChartImage) {
            $att = $mail.Attachments.Add($ChartImage, 1, 1, [IO.Path]::GetFileName($ChartImage))
            $pa = $att.PropertyAccessor
            # PR_ATTACH_CONTENT_ID (Unicode string)
            $pa.SetProperty("http://schemas.microsoft.com/mapi/proptag/0x3712001F", $ChartCid)
            # PR_ATTACHMENT_HIDDEN (boolean) -- don't list in the attachment tray
            $pa.SetProperty("http://schemas.microsoft.com/mapi/proptag/0x7FFE000B", $true)
            $mail.Save()
            $chartAttached = $true
        }

        $cidOut = $null
        if ($chartAttached) { $cidOut = $ChartCid }

        Write-ResultJson @{
            status         = "ok"
            entryId        = $mail.EntryID
            subject        = $mail.Subject
            size           = $mail.Size
            storedIn       = $mail.Parent.Name
            parent         = $mail.Parent.FolderPath
            chartRewritten = $chartRewritten
            chartAttached  = $chartAttached
            chartCid       = $cidOut
        }
        break
    }

    "verify" {
        if (-not $EntryID) { throw "verify requires -EntryID" }
        try {
            $item = $ns.GetItemFromID($EntryID)
            Write-ResultJson @{
                found       = $true
                subject     = $item.Subject
                parent      = $item.Parent.FolderPath
                savedAt     = $item.LastModificationTime.ToString("yyyy-MM-ddTHH:mm:ssK")
                size        = $item.Size
                unsentDraft = ($item.Sent -eq $false)
            }
        } catch {
            Write-ResultJson @{ found = $false; error = $_.Exception.Message }
            exit 1
        }
        break
    }

    "append" {
        if (-not $EntryID)         { throw "append requires -EntryID" }
        if (-not $HtmlSnippetPath) { throw "append requires -HtmlSnippetPath" }
        if (-not (Test-Path $HtmlSnippetPath)) { throw "snippet not found: $HtmlSnippetPath" }

        $snippet = [System.IO.File]::ReadAllText($HtmlSnippetPath, [System.Text.Encoding]::UTF8)
        $item = $ns.GetItemFromID($EntryID)
        $body = $item.HTMLBody
        $before = $body.Length

        $sentinel  = "</td></tr></table>`r`n</td></tr></table>`r`n</body></html>"
        $sentinel2 = "</td></tr></table>`n</td></tr></table>`n</body></html>"
        if ($body.Contains($sentinel)) {
            $newBody = $body.Replace($sentinel, "$snippet`r`n$sentinel")
            $mode = "crlf-sentinel"
        } elseif ($body.Contains($sentinel2)) {
            $newBody = $body.Replace($sentinel2, "$snippet`n$sentinel2")
            $mode = "lf-sentinel"
        } else {
            $newBody = $body -replace "(</body>\s*</html>\s*)$", "$snippet`r`n`$1"
            $mode = "pre-body-fallback"
        }

        if ($newBody -eq $body) {
            Write-ResultJson @{ status="noop"; reason="splice did not change body"; mode=$mode }
            exit 1
        }

        $item.HTMLBody = $newBody
        $item.Save()

        Write-ResultJson @{
            status     = "ok"
            mode       = $mode
            sizeBefore = $before
            sizeAfter  = $newBody.Length
            bytesAdded = ($newBody.Length - $before)
            entryId    = $item.EntryID
            savedAt    = $item.LastModificationTime.ToString("yyyy-MM-ddTHH:mm:ssK")
            parent     = $item.Parent.FolderPath
        }
        break
    }
}
