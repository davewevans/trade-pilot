# Logs in the Render Dashboard – Render Docs

# Logs in the Render Dashboard

**Want to stream logs to your observability provider?**

See [Streaming Render Service Logs](/docs/log-streams).

View, search, and filter your service's runtime logs from its **Logs** page in the [Render Dashboard](https://dashboard.render.com):

![Log explorer in the Render Dashboard](/docs-assets/7473e5151aa94709fc26425483095c391c148df1ac169c8293394557ded7b795/log-explorer.png)

With a [**Professional** workspace](/docs/professional-features) or higher, the log explorer also shows [HTTP request logs](#http-request-logs) for web services.

Use any combination of text search and [supported filters](#log-filters) to narrow results:

![Log explorer in the Render Dashboard](/docs-assets/ad5ec0839008398ebe7caa196caa4ef4be2bd275b1afbee0740b5329a70885c0/log-explorer-results.png)

Separately, you can view logs for any recent [deploy or one-off job](#logs-for-an-individual-deploy-or-job).

Render does not emit logs for [static sites](/docs/static-sites).

## [](#inspecting-a-log-line)Inspecting a log line

Log lines in the explorer display the following information:

![Log line format](/docs-assets/c41f79a4615a0524fd010bf5f97f7e2317f2b3abe6eea019519743eab509e011/log-line-format.png)

[HTTP request logs](#http-request-logs) display the request's HTTP method and status code instead of an instance ID.

Component

Description

**Level**

An icon representing the log level, such as `info`, `warning`, or `error`. Hidden for `info`\-level lines until you hover.

Hover to view the log level as text.

Supports the following values:

-   `debug`
-   `info`
-   `notice`
-   `warning`
-   `error`
-   `critical`
-   `alert`
-   `emergency`

**Timestamp**

The time of day the log was generated, in your local time zone.

Mouse over this value to view the _full_ timestamp in local, UTC, and Unix formats.

**Instance**

The identifier for the service instance that generated the log, surrounded by square brackets. Helpful for filtering logs for a [scaled service](/docs/scaling), or for pinpointing an instance swap during a deploy.

Click this value to add it as a search filter.

[HTTP request logs](#http-request-logs) are aggregated at the service level (not the individual instance level), so they do not display this value.

**Message**

The logged message.

[HTTP request logs](#http-request-logs) instead display the details for the corresponding HTTP request, such as:

-   HTTP method
-   Status code
-   Requested URL

## [](#log-filters)Log filters

When searching with the log explorer, you can filter results by the following (in addition to searching for an arbitrary string):

Filter

Description

Time range / Live tail

Limit results to a predefined range (such as **Last 24 hours**), specify a custom range, or select **Live tail** to view a live feed of recent logs.

The default displayed range is **Last hour**. Specify a different range using the dropdown in the upper right of the log explorer.

The maximum available range depends on your workspace's [log retention period](#retention-period).

`level`

The log level. Specify in the search box.

Supports the following values:

-   `debug`
-   `info`
-   `notice`
-   `warning`
-   `error`
-   `critical`
-   `alert`
-   `emergency`

`instance`

The ID of the service instance that generated the log. Helpful for filtering logs for a [scaled service](/docs/scaling), or for pinpointing an instance swap during a deploy.

Specify in the search box. You can also click the instance ID for any log line to add it as a filter.

`method`

**[HTTP request logs](#http-request-logs) only.** The HTTP method of a particular request (such as `GET` or `POST`).

Specify in the search box.

`status_code`

**[HTTP request logs](#http-request-logs) only.** The response code for a particular request (such as `200`, `404`, or `500`).

Specify in the search box.

`host`

**[HTTP request logs](#http-request-logs) only.** The destination domain of a particular request (such as `my-web-service.onrender.com`). Helpful if your service has multiple [custom domains](/docs/custom-domains) used by different clients.

Specify in the search box.

`path`

**[HTTP request logs](#http-request-logs) only.** The path of a particular request (such as `/api/orders` or `/blog/post/123`). Helpful for filtering logs for a specific resource or endpoint.

Specify in the search box.

## [](#wildcards-and-regular-expressions)Wildcards and regular expressions

The log explorer supports searching with wildcards and regular expressions.

To match any number of characters, use the wildcard token (`*`). To match against a regular expression, enclose your search in forward slashes (`/`). You can then use any metacharacters supported by the [RE2 syntax](https://github.com/google/re2/wiki/Syntax).

You can use wildcards and regular expressions in search strings and in filters. See the table below for some useful examples.

Search

Description

`foo*bar`

Returns logs that contain `foo` followed by `bar` using wildcard search.

`/foo.*bar/`

Returns logs that contain `foo` followed by `bar` using a regular expression.

`/(foo|bar)/`

Returns logs that contain `foo` or `bar`.

`status_code:/4../`

Returns request logs with a `4xx` status code.

`method:/(GET|POST)/`

Returns request logs with a `GET` or `POST` method.

`path:api/resource/*/subresource`

Returns request logs with a path that starts with `api/resource/` and ends with `/subresource`.

`/responseTimeMS=\d{3}\d+/`

Returns request logs with a response time greater than one second.

## [](#keyboard-shortcuts)Keyboard shortcuts

The log explorer supports these keyboard shortcuts:

Action

Shortcut

Focus search bar

`/`

Enable fullscreen

`M`

Exit fullscreen

`M` or `Esc`

Scroll (slow)

`Arrow Up` / `Arrow Down`

Scroll (fast)

`Page Up` / `Page Down`

Jump to top

`Home`

Jump to bottom

`End`

Copy all currently displayed logs

`CMD+Shift+C` (macOS)

`CTRL+Shift+C` (Windows/Linux)

Clear logs (live tail view only)

`Shift+L`

## [](#http-request-logs)HTTP request logs

If you have a [**Professional** workspace](/docs/professional-features) or higher, Render generates a log entry for each HTTP request to your team's web services from the public internet:

![HTTP request logs in the Render Dashboard](/docs-assets/7f7c5bb65357c300db0f854b0b57ec9b788729ed8f5154e693965b37f9ea15ec/http-request-logs.png)

This helps you debug unexpected behavior for a request, in particular by tracing its execution via the [`requestID` field](#tracing-with-requestid-and-rndr-id).

HTTP request logs appear alongside application logs in the explorer, and they support additional [filters](#log-filters) (such as `method` and `status_code`).

Render does _not_ generate request logs for HTTP requests sent from other services over your private network—only for requests sent to web services over the public internet.

### [](#tracing-with-requestid-and-rndr-id)Tracing with `requestID` and `Rndr-Id`

In each [HTTP request log entry](#http-request-logs), the value of the `requestID` field uniquely identifies the associated request:

logCopy to clipboard

11:24:03 \[GET\] example.com/api/orders clientIP="198.51.100.3" requestID="8ebfa3c3-8929-4885" ...

Render includes this same value in the `Rndr-Id` HTTP header—both in the request to your web service _and_ in the response to the requesting client:

httpCopy to clipboard

Rndr-Id: 58ebfa3c3-8929-4885

In your web service's code, you can extract this value from the header and include it in every log you generate for a given request. If you do, you can search for this ID in the log explorer to view the corresponding request's chronological log history.

On the client's side, here's what a `Rndr-Id` looks like in Chrome's Network panel:

![Viewing the Rndr-Id header in Chrome](/docs-assets/fb31144007fa1c47f216407bfafd3226e11dea68c720212cbbcf7734a12e812b/rndr-id-chrome.png)

By tracing each phase of the request lifecycle with one consistent ID, you can more quickly diagnose and debug issues in collaboration with the users who encounter them.

## [](#logs-for-an-individual-deploy-or-job)Logs for an individual deploy or job

View the logs for an individual deploy of your service from the service's **Events** page. Click the word **Deploy** in a timeline entry to open the log explorer:

![Selecting a deploy to view logs](/docs-assets/06867f682d0ddca104b7c45ce9dda33cae04cc7e3e5d3f5330b261ff80194937/deploy-activity.png)

⬇️

![Viewing logs for a single deploy](/docs-assets/d82e8fd8afdaa476204c8bc16b936861b41dd50111721933628b70aae78d8d7f/deploy-logs.png)

Similarly, you can view logs for the execution of a [one-off job](/docs/one-off-jobs) from the associated service's **Jobs** page.

## [](#explorer-theme)Explorer theme

The log explorer supports both light and dark display themes. It defaults to matching the theme that you [set for the Render Dashboard](/docs/render-dashboard#set-your-display-theme).

You can independently set the explorer's theme from the **Appearance** section of your [User Settings page](https://dashboard.render.com/u/settings#appearance):

![Log explorer theme settings in the Render Dashboard](/docs-assets/696c8994f91324011892a1ab63d9cd1ccd009ec34f9be7f2ee7e9966e124fe2c/log-viewer-theme-settings.png)

## [](#log-limits)Log limits

### [](#retention-period)Retention period

Render's log retention period depends on your workspace's plan (see the [pricing page](/pricing)):

Workspace Plan

Retention Period

Hobby

7 days

Professional

14 days

Organization / Enterprise

30 days

Logs older than your current retention period are no longer available, even if you upgrade your plan to extend the period.

If you need to retain logs for a longer period, you can [stream your logs to a syslog-compatible provider](/docs/log-streams).

### [](#rate-limit)Rate limit

Render processes a maximum of 6,000 application-generated log lines per minute for each running instance of a service.

If an instance generates logs in excess of this limit, Render drops the excess log lines. Dropped log lines don't appear in the log explorer or in [log streams](/docs/log-streams).

Copy page

###### [Logs in the Render Dashboard](/docs/logging)

-   [Inspecting a log line](#inspecting-a-log-line)
-   [Log filters](#log-filters)
-   [Wildcards and regular expressions](#wildcards-and-regular-expressions)
-   [Keyboard shortcuts](#keyboard-shortcuts)
-   [HTTP request logs](#http-request-logs)
    -   [Tracing with requestID and Rndr-Id](#tracing-with-requestid-and-rndr-id)
-   [Logs for an individual deploy or job](#logs-for-an-individual-deploy-or-job)
-   [Explorer theme](#explorer-theme)
-   [Log limits](#log-limits)
    -   [Retention period](#retention-period)
    -   [Rate limit](#rate-limit)

Did this page help?