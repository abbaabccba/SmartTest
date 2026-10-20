import { icon } from '../icons.js'
export function mount(root) {
  root.innerHTML = `
            <!-- Page Header -->
            <div class="page-header" style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 1rem;">
                <div>
                    <h1 class="greeting">Analytics</h1>
                    <p class="greeting-sub">Detailed insights and performance metrics</p>
                </div>
                <div style="display: flex; gap: 0.75rem; flex-wrap: wrap;">
                    <div class="date-picker role-segments">
                        <button class="date-btn" onclick="setDateRange('7d', this)">7D</button>
                        <button class="date-btn active" onclick="setDateRange('30d', this)">30D</button>
                        <button class="date-btn" onclick="setDateRange('90d', this)">90D</button>
                        <button class="date-btn" onclick="setDateRange('12m', this)">12M</button>
                    </div>
                    <button class="button button-secondary">
                        ${icon('download')}
                        Export
                    </button>
                </div>
            </div>

            <!-- Key Metrics Row -->
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">Page Views</div>
                    <div class="stat-value">128,450</div>
                    <div class="stat-change positive">${icon('trend-up')}+18.2% vs last period</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Unique Visitors</div>
                    <div class="stat-value">45,230</div>
                    <div class="stat-change positive">${icon('trend-up')}+12.5% vs last period</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Avg. Session</div>
                    <div class="stat-value">4m 32s</div>
                    <div class="stat-change positive">${icon('trend-up')}+8.7% vs last period</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Bounce Rate</div>
                    <div class="stat-value">32.4%</div>
                    <div class="stat-change negative">${icon('trend-down')}+2.1% vs last period</div>
                </div>
            </div>

            <!-- Charts Row -->
            <div class="two-col" style="margin-bottom: 1.5rem;">
                <!-- Traffic Comparison Chart -->
                <div class="card">
                    <div class="card-header">
                        <div>
                            <h3 class="card-title">Traffic Comparison</h3>
                            <p class="card-subtitle">This period vs previous period</p>
                        </div>
                    </div>
                    <div class="chart-container">
                        <div class="chart-scroll">
                            <div class="chart-scroll-inner">
                                <div class="bar-chart">
                                    <div class="y-axis">
                                        <span class="y-axis-label">5K</span>
                                        <span class="y-axis-label">4K</span>
                                        <span class="y-axis-label">3K</span>
                                        <span class="y-axis-label">2K</span>
                                        <span class="y-axis-label">1K</span>
                                        <span class="y-axis-label">0</span>
                                    </div>
                                    <div class="y-axis-lines">
                                        <div class="y-axis-line"></div>
                                        <div class="y-axis-line"></div>
                                        <div class="y-axis-line"></div>
                                        <div class="y-axis-line"></div>
                                        <div class="y-axis-line"></div>
                                        <div class="y-axis-line"></div>
                                    </div>
                                    <div class="bar-group"><div class="bar-wrapper"><div class="bar previous" style="height: 90px;"></div><div class="bar current" style="height: 120px;"></div></div><span class="bar-label">Mon</span></div>
                                    <div class="bar-group"><div class="bar-wrapper"><div class="bar previous" style="height: 100px;"></div><div class="bar current" style="height: 140px;"></div></div><span class="bar-label">Tue</span></div>
                                    <div class="bar-group"><div class="bar-wrapper"><div class="bar previous" style="height: 85px;"></div><div class="bar current" style="height: 110px;"></div></div><span class="bar-label">Wed</span></div>
                                    <div class="bar-group"><div class="bar-wrapper"><div class="bar previous" style="height: 110px;"></div><div class="bar current" style="height: 145px;"></div></div><span class="bar-label">Thu</span></div>
                                    <div class="bar-group"><div class="bar-wrapper"><div class="bar previous" style="height: 130px;"></div><div class="bar current" style="height: 155px;"></div></div><span class="bar-label">Fri</span></div>
                                    <div class="bar-group"><div class="bar-wrapper"><div class="bar previous" style="height: 75px;"></div><div class="bar current" style="height: 95px;"></div></div><span class="bar-label">Sat</span></div>
                                    <div class="bar-group"><div class="bar-wrapper"><div class="bar previous" style="height: 65px;"></div><div class="bar current" style="height: 85px;"></div></div><span class="bar-label">Sun</span></div>
                                </div>
                            </div>
                        </div>
                        <div class="chart-legend">
                            <div class="legend-item"><span class="legend-dot current"></span>This Period</div>
                            <div class="legend-item"><span class="legend-dot previous"></span>Previous Period</div>
                        </div>
                    </div>
                </div>

                <!-- Traffic Sources -->
                <div class="card">
                    <div class="card-header">
                        <div>
                            <h3 class="card-title">Traffic Sources</h3>
                            <p class="card-subtitle">Where your visitors come from</p>
                        </div>
                    </div>
                    <div class="chart-scroll">
                        <div class="chart-scroll-inner" style="min-width: 400px;">
                            <div style="display: flex; align-items: center; gap: 2rem; padding: 1rem 0;">
                                <div class="donut-chart">
                                    <svg viewBox="0 0 36 36">
                                        <circle class="donut-ring" cx="18" cy="18" r="15.9"></circle>
                                        <circle class="donut-segment" cx="18" cy="18" r="15.9" stroke="var(--accent)" stroke-dasharray="42 100" stroke-dashoffset="0"></circle>
                                        <circle class="donut-segment" cx="18" cy="18" r="15.9" stroke="var(--success)" stroke-dasharray="28 100" stroke-dashoffset="-42"></circle>
                                        <circle class="donut-segment" cx="18" cy="18" r="15.9" stroke="var(--warning)" stroke-dasharray="18 100" stroke-dashoffset="-70"></circle>
                                        <circle class="donut-segment" cx="18" cy="18" r="15.9" stroke="#A855F7" stroke-dasharray="12 100" stroke-dashoffset="-88"></circle>
                                    </svg>
                                    <div class="donut-center"><div class="donut-value">45K</div><div class="donut-label">Visitors</div></div>
                                </div>
                                <div style="flex: 1;">
                                    <div style="margin-bottom: 1rem;"><div style="display: flex; justify-content: space-between; margin-bottom: 0.25rem;"><span style="font-size: 0.875rem; color: var(--text-primary);">Direct</span><span style="font-size: 0.875rem; font-weight: 600; color: var(--text-primary);">42%</span></div><div class="progress-bar"><div class="progress-fill accent" style="width: 42%;"></div></div></div>
                                    <div style="margin-bottom: 1rem;"><div style="display: flex; justify-content: space-between; margin-bottom: 0.25rem;"><span style="font-size: 0.875rem; color: var(--text-primary);">Organic Search</span><span style="font-size: 0.875rem; font-weight: 600; color: var(--text-primary);">28%</span></div><div class="progress-bar"><div class="progress-fill success" style="width: 28%;"></div></div></div>
                                    <div style="margin-bottom: 1rem;"><div style="display: flex; justify-content: space-between; margin-bottom: 0.25rem;"><span style="font-size: 0.875rem; color: var(--text-primary);">Social Media</span><span style="font-size: 0.875rem; font-weight: 600; color: var(--text-primary);">18%</span></div><div class="progress-bar"><div class="progress-fill warning" style="width: 18%;"></div></div></div>
                                    <div><div style="display: flex; justify-content: space-between; margin-bottom: 0.25rem;"><span style="font-size: 0.875rem; color: var(--text-primary);">Referral</span><span style="font-size: 0.875rem; font-weight: 600; color: var(--text-primary);">12%</span></div><div class="progress-bar"><div class="progress-fill" style="width: 12%; background: #A855F7;"></div></div></div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Top Pages Table -->
            <div class="card">
                <div class="card-header">
                    <div>
                        <h3 class="card-title">Top Pages</h3>
                        <p class="card-subtitle">Most visited pages this period</p>
                    </div>
                    <button class="button button-ghost">View All</button>
                </div>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr><th>Page</th><th>Views</th><th>Unique Views</th><th>Avg. Time</th><th>Bounce Rate</th><th>Change</th></tr>
                        </thead>
                        <tbody>
                            <tr><td><div style="font-weight: 500;">/dashboard</div><div style="font-size: 0.8125rem; color: var(--text-secondary);">Main dashboard</div></td><td>15,230</td><td>12,450</td><td>5m 24s</td><td>24.5%</td><td><span class="badge badge-green">+12.4%</span></td></tr>
                            <tr><td><div style="font-weight: 500;">/analytics</div><div style="font-size: 0.8125rem; color: var(--text-secondary);">Analytics page</div></td><td>8,450</td><td>6,890</td><td>4m 18s</td><td>28.3%</td><td><span class="badge badge-green">+8.7%</span></td></tr>
                            <tr><td><div style="font-weight: 500;">/projects</div><div style="font-size: 0.8125rem; color: var(--text-secondary);">Projects overview</div></td><td>6,780</td><td>5,420</td><td>3m 52s</td><td>31.2%</td><td><span class="badge badge-green">+5.2%</span></td></tr>
                            <tr><td><div style="font-weight: 500;">/settings</div><div style="font-size: 0.8125rem; color: var(--text-secondary);">User settings</div></td><td>4,120</td><td>3,890</td><td>2m 45s</td><td>45.8%</td><td><span class="badge badge-red">-2.3%</span></td></tr>
                            <tr><td><div style="font-weight: 500;">/inbox</div><div style="font-size: 0.8125rem; color: var(--text-secondary);">Messages inbox</div></td><td>3,890</td><td>3,120</td><td>6m 12s</td><td>18.4%</td><td><span class="badge badge-green">+15.8%</span></td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        `
  return { start() {}, destroy() {} }
}
