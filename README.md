<p align="center">
  <img src="https://smartinghome.pl/wp-content/uploads/2026/03/smartinghome_banner_1774113402088.jpg" alt="Smarting HOME — Autonomous Energy Management" width="480" style="max-width:480px;border-radius:12px;"/>
</p>

<h1 align="center">Smarting HOME — Autonomous Energy Management</h1>

<p align="center">
  <strong>Professional AI-powered Home Energy Management System for Home Assistant</strong>
</p>

<p align="center">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=white" alt="HACS" /></a>
  <a href="https://www.home-assistant.io"><img src="https://img.shields.io/badge/Home%20Assistant-2025.1+-18BCF2?style=for-the-badge&logo=homeassistant&logoColor=white" alt="Home Assistant" /></a>
  <a href="https://smartinghome.pl"><img src="https://img.shields.io/badge/License-Commercial-E74C3C?style=for-the-badge&logo=keycdn&logoColor=white" alt="License" /></a>
  <img src="https://img.shields.io/badge/Version-1.21.5-2ECC71?style=for-the-badge" alt="Version" />
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
</p>

<p align="center">
  <a href="https://smartinghome.pl">🌐 Website</a> •
  <a href="https://smartinghome.pl/docs">📖 Documentation</a> •
  <a href="https://github.com/GregECAT/smartinghome-homeassistant/issues">🐛 Issues</a> •
  <a href="https://smartinghome.pl/buy">🛒 Buy License</a>
</p>

<p align="center">
  <sub>Created by <a href="https://github.com/GregECAT"><strong>GregECAT</strong></a> • <a href="https://smartinghome.pl"><strong>Smarting HOME</strong></a> 🇵🇱</sub>
</p>

---

## 🎯 What is Smarting HOME?

**Smarting HOME** is a commercial-grade HACS integration that transforms your Home Assistant into an **intelligent energy command center**. It autonomously manages solar production, battery storage, grid trading, and home loads — maximizing savings on the Polish energy market.

> 💡 **Built for real-world Polish prosumers** — optimized for G13 tariff, RCE dynamic pricing, net-billing, GoodWe inverters, and Lynx batteries.

---

## ✨ Key Features

<table>
  <tr>
    <td width="50%">
      <h3>🧠 3-Layer HEMS Engine</h3>
      <p>Autonomous decision making with three strategy layers:</p>
      <ul>
        <li><strong>W1 — G13 Schedule</strong>: Time-based tariff optimization</li>
        <li><strong>W2 — RCE Dynamic</strong>: Real-time market price reactions</li>
        <li><strong>W3 — SOC Safety</strong>: Battery and grid protection</li>
      </ul>
    </td>
    <td width="50%">
      <h3>🤖 AI Energy Advisor</h3>
      <p>Integrated AI for intelligent recommendations:</p>
      <ul>
        <li><strong>Google Gemini 2.5 Pro</strong> — Energy optimization advice</li>
        <li><strong>Anthropic Claude Sonnet</strong> — Natural language reports</li>
        <li>Anomaly detection & forecast-aware decisions</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td>
      <h3>💰 Financial Optimization</h3>
      <ul>
        <li>G13 tariff zones: off-peak <code>0.63</code>, morning <code>0.91</code>, afternoon <code>1.50</code> PLN/kWh</li>
        <li>RCE PSE dynamic sell prices with trend analysis</li>
        <li>Night arbitrage: charge at 0.63, sell at 1.50 PLN = <strong>~177 PLN/month</strong></li>
        <li>G13 vs RCE spread calculation & sell evaluation</li>
      </ul>
    </td>
    <td>
      <h3>🛡️ Safety Cascades</h3>
      <ul>
        <li><strong>Voltage protection</strong>: 252V → 253V → 254V tiered response</li>
        <li><strong>PV surplus</strong>: 2kW → 3kW → 4kW progressive load activation</li>
        <li><strong>SOC emergency</strong>: Force charge at &lt; 20%</li>
        <li><strong>SOC checkpoints</strong>: 11:00 (50%) and 12:00 (70%)</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td>
      <h3>📊 100+ Sensors & Entities</h3>
      <ul>
        <li>33 computed sensors (grid, tariff, RCE, forecasts, economics)</li>
        <li>3 binary sensors (peak detection, license status)</li>
        <li>5 control switches (auto mode, arbitrage, cascade)</li>
        <li>2 selects (EMS mode, strategy) + 3 number controls</li>
      </ul>
    </td>
    <td>
      <h3>🎨 Ready-to-Use Dashboards</h3>
      <ul>
        <li><strong>⚡ Power Flow</strong> — Animated Sunsynk card (3-phase, 2 MPPT)</li>
        <li><strong>🤖 HEMS Control</strong> — Full control panel with quick actions</li>
        <li><strong>💲 Economics</strong> — Financial analytics & arbitrage calculator</li>
      </ul>
    </td>
  </tr>
</table>

---

## 🔧 Supported Hardware

- **Inverter**: GoodWe BT series (3-phase hybrid) — *UDP / Modbus RS485*
- **Battery**: Lynx Home U 10.2 kWh (LiFePO4) — *Via inverter*
- **Grid**: Polish market (Tauron, Enea, PGE, Energa) — *G13 / G11 / C-tariff*
- **Market Data**: RCE PSE dynamic pricing — *REST API*
- **Forecast**: Solcast / Forecast.Solar — *HA integration*

---

## 📦 Installation

### Via HACS (Recommended)

1. Open **HACS** in your Home Assistant instance
2. Click the **⋮** menu → **Custom repositories**
3. Add URL: `https://github.com/GregECAT/smartinghome-homeassistant`
4. Select category: **Integration**
5. Search for "**Smarting HOME**" and click **Download**
6. **Restart Home Assistant**

### Manual Installation

```bash
# Clone the repository
git clone https://github.com/GregECAT/smartinghome-homeassistant.git

# Copy to your HA config directory
cp -r smartinghome-homeassistant/custom_components/smartinghome /config/custom_components/

# Restart Home Assistant
```

---

## ⚙️ Setup Wizard

After installation, add the integration via **Settings → Devices & Services → Add Integration → Smarting HOME**.

The setup wizard guides you through:

### Step 1 — Choose License Mode 🔑

Choose between **FREE** (no license needed) or **PRO** (license key required):

| Feature | 🆓 FREE | ⭐ PRO | 🏢 ENTERPRISE |
|---------|---------|--------|---------------|
| **Sensors** (33) | ✅ All | ✅ All | ✅ All |
| **G13 Tariff + RCE** | ✅ | ✅ | ✅ |
| **HEMS Auto Mode** | ✅ | ✅ | ✅ |
| **Night Arbitrage** | ❌ | ✅ | ✅ |
| **Voltage/PV Cascades** | ❌ | ✅ | ✅ |
| **AI Advisor** | ❌ | ✅ | ✅ |
| **Export Limit Control** | ❌ | ✅ | ✅ |
| **Blueprints** | 2 basic | 7 full | 7 full |
| **Dashboards** | 1 basic | 3 full | 3 full |
| **Multi-site** | ❌ | ❌ | ✅ |

> 💡 **FREE mode works immediately** — no license server, no registration needed.

### Step 2 — Inverter Configuration ⚡

Enter your GoodWe device ID (found in GoodWe integration). Optionally enable Modbus RS485 for extended diagnostics.

### Step 3 — Tariff Selection 💶

Choose your electricity tariff:
- **G13** (recommended) — 3-zone time-of-use with winter/summer schedules
- **G11** — Single rate
- **C-tariff** — Commercial rates

Enable **RCE PSE** dynamic pricing for real-time market data.

### Step 4 — AI Configuration 🤖 *(PRO only, optional)*

Provide your own API keys for AI energy advisory:
- **Google Gemini**: [Get key at ai.google.dev](https://ai.google.dev/)
- **Anthropic Claude**: [Get key at console.anthropic.com](https://console.anthropic.com/)

---

## 🔄 Upgrade FREE → PRO

Already installed as FREE? You can upgrade to PRO **without reinstalling**:

1. Go to **Settings → Devices & Services → Smarting HOME**
2. Click **Configure** (⚙️ gear icon)
3. Enter your PRO license key in the **🔑 License Key** field
4. Click **Submit** — license is validated automatically
5. **Restart Home Assistant** to activate all PRO features

> 💡 Buy PRO license at [smartinghome.pl/buy](https://smartinghome.pl/buy)

After upgrade you'll unlock: Night Arbitrage, Voltage/PV Cascades, AI Advisor, Export Limit Control, all 7 Blueprints, 3 full Dashboards.

---

## 🖥️ Dashboards

### Automatic — Sidebar Panel

After setup, **Smarting HOME** panel appears automatically in the HA sidebar with 5 tabs:
- **📊 Przegląd** — Live PV/Grid/Battery/Load, SOC bar, daily stats
- **⚡ Energia** — 3-phase voltage, PV forecast, quick actions
- **💰 Taryfy & RCE** — G13 zones, RCE prices, trends
- **🔋 Bateria** — SOC, energy, runtime, arbitrage calculator
- **🤖 HEMS** — Mode control, AI advisor, reports

> Click **⊞ Pełny ekran** for fullscreen (kiosk) mode — perfect for wall-mounted tablets.

### Manual — YAML Dashboards

For additional dashboards, add to `configuration.yaml`:

```yaml
lovelace:
  dashboards:
    smartinghome-hems:
      mode: yaml
      title: "HEMS Control"
      icon: mdi:brain
      show_in_sidebar: true
      filename: custom_components/smartinghome/dashboards/hems_control.yaml
    smartinghome-power:
      mode: yaml
      title: "Power Flow"
      icon: mdi:solar-power-variant
      show_in_sidebar: true
      filename: custom_components/smartinghome/dashboards/power_flow.yaml
    smartinghome-economics:
      mode: yaml
      title: "Economics"
      icon: mdi:currency-usd
      show_in_sidebar: true
      filename: custom_components/smartinghome/dashboards/economics.yaml
```

Restart Home Assistant after adding.

---

## 📊 HEMS Strategy — Trójwarstwowy System

```
┌─────────────────────────────────────────────────────────────┐
│                    SMARTING HOME HEMS                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─── W3 — SAFETY ──────────────────────────────────────┐   │
│  │  SOC Emergency (<20%) │ Voltage Cascade │ SOC Checks  │   │
│  │  ↓ overrides W1+W2                                    │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── W2 — RCE DYNAMIC ─────────────────────────────────┐   │
│  │  Real-time market prices │ Cheapest/Expensive windows │   │
│  │  ↓ adjusts W1 decisions                               │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── W1 — G13 SCHEDULE ────────────────────────────────┐   │
│  │  07:00 Sell │ 13:00 Charge │ 16:00 Peak │ 21:00 Night │   │
│  │  Base layer — time-of-use optimization                │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### W1 — G13 Schedule (Taryfa czasowa)

| Time (Winter) | Zone | Price | Action |
|--------------|------|-------|--------|
| `07:00–13:00` | ☀️ Morning peak | 0.91 PLN/kWh | Block charging, export PV to grid |
| `13:00–16:00` | 🔋 Off-peak | 0.63 PLN/kWh | Charge battery from PV |
| `16:00–21:00` | 💰 Afternoon peak | 1.50 PLN/kWh | Battery → Home, minimize import |
| `21:00–07:00` | 🌙 Off-peak | 0.63 PLN/kWh | Auto-consumption, night arbitrage |
| Weekends | 🌙 Off-peak | 0.63 PLN/kWh | All day off-peak |

> ℹ️ Summer schedule: Peak shifts to 19:00–22:00

### W2 — RCE Dynamic (Ceny dynamiczne)

| RCE Price | Level | Action |
|-----------|-------|--------|
| `< 0 PLN/MWh` | 💚 Negative | Charge + enable all loads (free energy!) |
| `< 150 PLN/MWh` | 🟢 Cheap | Prioritize grid charging |
| `150–300 PLN/MWh` | 🟡 Normal | Standard operation |
| `300–500 PLN/MWh` | 🟠 Expensive | Sell energy, minimize import |
| `> 500 PLN/MWh` | 🔴 Very expensive | Maximum export, disable loads |

### W3 — SOC Safety (Bezpieczeństwo)

| Condition | Trigger | Action |
|-----------|---------|--------|
| SOC < 20% | Immediate | ⚠️ Emergency charge |
| SOC < 50% at 11:00 | Checkpoint | Enable charging |
| SOC < 70% at 12:00 | Checkpoint | Enable charging |
| Voltage > 252V | Cascade T1 | Boiler ON |
| Voltage > 253V | Cascade T2 | Boiler + AC ON |
| Voltage > 254V | Cascade T3 | All loads + restore charging |
| Voltage < 248V (5 min) | Recovery | Cascade OFF |

---

## 🤖 AI Energy Advisor

The AI Advisor uses your live system data to provide context-aware energy management recommendations.

```yaml
# Ask AI for real-time optimization advice
service: smartinghome.ask_ai_advisor
data:
  question: "Should I export energy to the grid right now?"
  provider: auto  # auto | gemini | anthropic

# Generate a comprehensive daily report
service: smartinghome.generate_report
```

**Capabilities:**
- ⚡ Real-time charge/discharge optimization
- 📊 AI-generated daily energy reports
- 🔍 Anomaly detection in usage patterns
- 🌤️ Forecast-aware scheduling recommendations
- 💰 Arbitrage opportunity identification

> 🔐 **Requires PRO license** and your own AI API key (Google or Anthropic)

---

## 🔧 Services

- ⚙️ **`smartinghome.set_mode`**<br/>Zmień tryb HEMS: auto, sell, charge, peak_save, night_arbitrage, emergency, manual *(DEMO+)*
- 🔋 **`smartinghome.force_charge`**<br/>Force battery charging at max current (18.5A) *(DEMO+)*
- ⚡ **`smartinghome.force_discharge`**<br/>Force battery discharge (block charging) *(DEMO+)*
- 🔌 **`smartinghome.set_export_limit`**<br/>Set grid export power limit (0–16000W) *(PRO+)*
- 🤖 **`smartinghome.ask_ai_advisor`**<br/>Query AI energy advisor with a question *(PRO+)*
- 📊 **`smartinghome.generate_report`**<br/>Generate AI-powered daily energy report *(PRO+)*

---

## 📋 Automation Blueprints

7 ready-to-use automation blueprints included:

- ☀️ **Morning Sell Mode** `[Layer W1]`<br/>*Schedule: 07:00 weekdays* — Block charging, enable max export.
- 🔋 **Midday Charge Mode** `[Layer W1]`<br/>*Schedule: 13:00 weekdays* — Enable battery charging from PV.
- 🌙 **Night Arbitrage** `[Layer W1]`<br/>*Schedule: 23:00 daily* — Off-peak charge for peak discharge.
- 💰 **RCE Cheapest Window** `[Layer W2]`<br/>*Schedule: Dynamic* — Auto-charge during cheapest prices.
- 🔥 **RCE Expensive Alert** `[Layer W2]`<br/>*Schedule: Dynamic* — Alert during peak prices.
- ☀️ **PV Surplus Cascade** `[Layer W3]`<br/>*Schedule: Dynamic* — Progressive load activation (2/3/4 kW).
- ⚡ **Voltage Protection** `[Layer W3]`<br/>*Schedule: Dynamic* — Cascade protection (252/253/254V).
- ⚠️ **SOC Emergency** `[Layer W3]`<br/>*Schedule: Dynamic* — Emergency charge when SOC < 20%.

All blueprints are located in `custom_components/smartinghome/blueprints/`.

---

## 🎨 Dashboards

Three pre-configured Lovelace dashboard configs:

| Dashboard | Description | Required HACS Cards |
|-----------|-------------|-------------------|
| ⚡ **Power Flow** | Animated 3-phase power flow with 2 MPPT | [sunsynk-power-flow-card](https://github.com/slipx06/sunsynk-power-flow-card) |
| 🤖 **HEMS Control** | Full system controls, G13/RCE status, AI buttons | None (core) |
| 💲 **Economics** | Financial analytics, autarky, arbitrage calculator | None (core) |

Dashboard YAML files: `custom_components/smartinghome/dashboards/`

---

## 🌍 Internationalization

| Language | Config Flow | Entities | Errors |
|----------|------------|----------|--------|
| 🇬🇧 English | ✅ | ✅ | ✅ |
| 🇵🇱 Polish | ✅ | ✅ | ✅ |

---

## 📁 Repository Structure

```
smartinghome-homeassistant/
├── .github/workflows/validate.yml       # CI/CD pipeline
├── assets/banner.png                    # Brand assets
├── hacs.json                            # HACS manifest
├── LICENSE                              # Commercial license
├── README.md                            # This file
├── info.md                              # HACS store page
└── custom_components/smartinghome/
    ├── __init__.py                      # Integration entry point
    ├── api.py                           # License API client
    ├── ai_advisor.py                    # Gemini + Claude advisor
    ├── binary_sensor.py                 # Binary sensor platform
    ├── config_flow.py                   # 4-step setup wizard
    ├── const.py                         # Constants & definitions
    ├── coordinator.py                   # Data coordinator (60+ sensors)
    ├── energy_manager.py                # 3-layer HEMS engine
    ├── license.py                       # License management
    ├── manifest.json                    # HA integration manifest
    ├── number.py                        # Number platform
    ├── select.py                        # Select platform
    ├── sensor.py                        # Sensor platform (33 sensors)
    ├── services.py                      # Service handlers
    ├── services.yaml                    # Service definitions
    ├── strings.json                     # Default strings
    ├── switch.py                        # Switch platform
    ├── translations/                    # i18n
    │   ├── en.json
    │   └── pl.json
    ├── blueprints/                      # Automation blueprints
    │   ├── hems_morning_sell_mode.yaml
    │   ├── hems_midday_charge_mode.yaml
    │   ├── hems_night_arbitrage.yaml
    │   ├── hems_soc_emergency.yaml
    │   ├── hems_voltage_protection.yaml
    │   ├── hems_rce_cheapest_window.yaml
    │   └── hems_pv_surplus.yaml
    └── dashboards/                      # Lovelace configs
        ├── power_flow.yaml
        ├── hems_control.yaml
        └── economics.yaml
```

---

## 📞 Support & Contact

| Channel | Link |
|---------|------|
| 🌐 Website | [smartinghome.pl](https://smartinghome.pl) |
| 🛒 Buy License | [smartinghome.pl/buy](https://smartinghome.pl/buy) |
| 📖 Documentation | [smartinghome.pl/docs](https://smartinghome.pl/docs) |
| 🐛 Bug Reports | [GitHub Issues](https://github.com/GregECAT/smartinghome-homeassistant/issues) |
| 📧 Email | [kontakt@smartinghome.pl](mailto:kontakt@smartinghome.pl) |
| 💬 Community | [Home Assistant Community](https://community.home-assistant.io/) |

---

## 👨‍💻 Author

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/GregECAT">
        <img src="https://github.com/GregECAT.png" width="100px;" alt="GregECAT"/>
        <br />
        <sub><b>GregECAT</b></sub>
      </a>
      <br />
      <sub>Founder • Developer • Energy Enthusiast</sub>
      <br />
      <sub><a href="https://smartinghome.pl">Smarting HOME</a> 🇵🇱</sub>
    </td>
  </tr>
</table>

---

## 📄 License

This software is proprietary and protected under the **Smarting HOME Commercial License**.

A valid license key is required for PRO and ENTERPRISE features. DEMO mode is available for evaluation with limited functionality.

See the full [LICENSE](./LICENSE) file for details.

---

<p align="center">
  <sub>
    <strong>© 2026 Smarting HOME</strong> by <a href="https://github.com/GregECAT">GregECAT</a>
    <br/>
    Built with ❤️ for the Polish energy market
    <br/><br/>
    <a href="https://smartinghome.pl">smartinghome.pl</a>
  </sub>
</p>
