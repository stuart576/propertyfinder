# Property Finder 🏡

A self-hosted property alert aggregator that monitors an M365 shared mailbox for property portal alerts, deduplicates listings across portals, and serves them through a clean web dashboard.

Built for finding rural detached properties with land in Herefordshire and Worcestershire.

## How It Works

1. **You** set up saved search alerts on Rightmove, Zoopla, OnTheMarket (and optionally specialist sites) — all pointing to a shared mailbox on your M365 tenant
2. **Property Finder** monitors that mailbox via the Microsoft Graph API, parses the alert emails, extracts listings, and deduplicates them
3. **You** browse a unified dashboard showing all matches, with the ability to star, hide, and annotate properties

## Supported Portals

- **Rightmove** — rightmove.co.uk saved searches
- **Zoopla** — zoopla.co.uk saved searches
- **OnTheMarket** — onthemarket.com saved searches
- **Smallholdings For Sale** — smallholdingsforsale.co.uk
- **UK Land & Farms** — uklandandfarms.co.uk
- **Grant & Co** — grantco.co (via generic parser)
- Any other portal that sends HTML alert emails (generic fallback parser)

## Setup

### 1. Create a Shared Mailbox

In the Microsoft 365 admin centre (admin.microsoft.com):

1. Go to **Teams & groups → Shared mailboxes**
2. Click **Add a shared mailbox**
3. Name: `Property Alerts`, address: `property-alerts@yourdomain.co.uk`
4. Click **Save**

### 2. Register an App in Entra ID

Go to entra.microsoft.com:

1. **App registrations → New registration**
2. Name: `Property Finder`
3. Account type: **Single tenant**
4. Redirect URI: Leave blank
5. Click **Register**

Note the **Application (client) ID** and **Directory (tenant) ID**.

### 3. Create a Client Secret

In your app registration:

1. **Certificates & secrets → Client secrets → New client secret**
2. Description: `Property Finder`, Expiry: 24 months
3. **Copy the secret value immediately** — you won't see it again

### 4. Grant API Permissions

In your app registration:

1. **API permissions → Add a permission → Microsoft Graph → Application permissions**
2. Add: **Mail.Read**
3. Click **Grant admin consent for [your org]**

### 5. Scope Access to the Shared Mailbox Only

In Exchange Online PowerShell:

```powershell
Connect-ExchangeOnline

# Create a scoping group
New-DistributionGroup -Name "Property Finder Access" -Type Security
Add-DistributionGroupMember -Identity "Property Finder Access" -Member "property-alerts@yourdomain.co.uk"

# Restrict the app to only this mailbox
New-ApplicationAccessPolicy -AppId "YOUR_CLIENT_ID" -PolicyScopeGroupId "Property Finder Access" -AccessRight RestrictAccess -Description "Property Finder app"

# Verify
Test-ApplicationAccessPolicy -AppId "YOUR_CLIENT_ID" -Identity "property-alerts@yourdomain.co.uk"
```

### 6. Set Up Saved Searches

Go to each portal and create saved searches with email alerts, all pointing to your shared mailbox address.

**Rightmove:**
- Property for sale → Herefordshire → Detached → 3+ beds → Max £850,000 → Include "land" keyword
- Property for sale → Worcestershire → Detached → 3+ beds → Max £850,000 → Include "land" keyword
- Land & commercial → Herefordshire → Farms/land
- Land & commercial → Worcestershire → Farms/land

**Zoopla:**
- For sale → Herefordshire → Detached → 3+ beds → Up to £850k → Farms/land
- For sale → Worcestershire → Detached → 3+ beds → Up to £850k → Farms/land

**OnTheMarket:**
- Farms & land → Herefordshire → Up to £850k
- Farms & land → Worcestershire → Up to £850k

**Specialist sites:**
- smallholdingsforsale.co.uk — sign up for alerts
- uklandandfarms.co.uk — email alerts for Herefordshire & Worcestershire

### 7. Deploy on Unraid

**Via Docker Compose Manager plugin:**

1. Copy the `property-finder` folder to `/mnt/user/appdata/property-finder`
2. Create data dir: `mkdir -p /mnt/user/appdata/property-finder/data`
3. In Unraid Docker tab → Add New Stack → Name: `property-finder`
4. Edit Stack → Compose File, paste:

```yaml
services:
  property-finder:
    build: /mnt/user/appdata/property-finder
    container_name: property-finder
    restart: unless-stopped
    ports:
      - "8585:8585"
    volumes:
      - /mnt/user/appdata/property-finder/data:/data
    environment:
      - GRAPH_TENANT_ID=your-tenant-id
      - GRAPH_CLIENT_ID=your-client-id
      - GRAPH_CLIENT_SECRET=your-client-secret
      - GRAPH_MAILBOX=property-alerts@yourdomain.co.uk
      - CHECK_INTERVAL=3600
      - MAX_PRICE=850000
      - MIN_BEDROOMS=3
      - MIN_ACRES=2.0
      - WEB_PORT=8585
```

5. Click **Compose Up**
6. Enable **Autostart**
7. Access dashboard at `http://your-unraid-ip:8585`

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAPH_TENANT_ID` | (required) | Entra ID tenant ID |
| `GRAPH_CLIENT_ID` | (required) | App registration client ID |
| `GRAPH_CLIENT_SECRET` | (required) | App registration client secret |
| `GRAPH_MAILBOX` | (required) | Shared mailbox address |
| `CHECK_INTERVAL` | `3600` | Seconds between inbox checks |
| `MAX_PRICE` | `850000` | Maximum price filter |
| `MIN_BEDROOMS` | `3` | Minimum bedroom filter |
| `MIN_ACRES` | `2.0` | Minimum acreage filter |
| `WEB_PORT` | `8585` | Dashboard port |
| `DB_PATH` | `/data/properties.db` | SQLite database path |

## Dashboard Features

- **Property cards** with price, beds, acres, location, and source portal
- **Star** properties you're interested in
- **Dismiss** properties you've ruled out
- **Notes** on each property for your comments
- **Sort** by newest, price, or land size
- **Filter views** for all, starred, or hidden
- **Manual check** button to trigger an immediate email scan
- **Deduplication** — same property listed on multiple portals shows once

## Architecture

```
M365 Shared Mailbox
    ↓ (Graph API, hourly)
Email Monitor (client credentials OAuth2)
    ↓ (HTML parsing)
Portal Parsers (Rightmove, Zoopla, OTM, Generic)
    ↓ (filter + dedup)
SQLite Database
    ↓
Flask Dashboard → Browser
```

## Troubleshooting

**Auth errors / 401 responses:**
- Verify tenant ID, client ID, and secret are correct
- Ensure admin consent has been granted for Mail.Read
- Check the application access policy allows access to the shared mailbox
- Client secrets expire — check the expiry date in Entra ID

**No emails being processed:**
- Send a test email to the shared mailbox and hit "Check Now"
- Check container logs: `docker logs property-finder`
- Verify the mailbox address in GRAPH_MAILBOX matches exactly

**Listings not being extracted:**
- Portal email formats change occasionally — check logs for parser errors
- The generic parser will catch most formats as a fallback
- Properties outside your filter criteria are silently skipped

**Access policy not working (can read all mailboxes):**
- Application access policies can take up to 30 minutes to propagate
- Run `Test-ApplicationAccessPolicy` to verify
- Ensure the distribution group is mail-enabled (Type: Security)
