import { themeToggle } from '../app-shell.js'

export function mount(root) {
  root.innerHTML = `
            <div class="page-header">
                <h1 class="greeting">Settings</h1>
                <p class="greeting-sub">Manage your account and preferences</p>
            </div><!-- Two Column Settings Layout -->
            <div class="settings-grid">
                <!-- Left Column: Account & Appearance -->
                <div>
                    <section class="settings-section">
                        <div class="card">
                            <h2 class="settings-title">Account Information</h2>
                            <p class="settings-desc">Update your personal information</p>
                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1rem;">
                                <div class="form-group" style="margin-bottom: 0;"><label class="form-label">First Name</label><input type="text" class="form-control" value=""></div>
                                <div class="form-group" style="margin-bottom: 0;"><label class="form-label">Last Name</label><input type="text" class="form-control" value="Johnson"></div>
                            </div>
                            <div class="form-group"><label class="form-label">Email Address</label><input type="email" class="form-control" value="alex.johnson@company.com"></div>
                            <div class="form-group"><label class="form-label">Job Title</label><input type="text" class="form-control" value="Product Manager"></div>
                            <div class="form-group" style="margin-bottom: 0;"><label class="form-label">Bio</label><textarea class="form-control" style="min-height: 80px; resize: vertical;">Product manager with 5+ years of experience in SaaS.</textarea></div>
                            <div style="margin-top: 1.25rem;"><button class="button button-primary">Save Changes</button></div>
                        </div>
                    </section>

                    <section class="settings-section">
                        <div class="card">
                            <h2 class="settings-title">Appearance</h2>
                            <p class="settings-desc">Customize how the dashboard looks</p>
                            <div class="settings-row">
                                <div class="settings-row-info"><div class="settings-row-label">Theme Mode</div><div class="settings-row-desc">Snow (light) or Carbon (dark)</div></div>
                                ${themeToggle()}
                            </div>
                            <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Compact Mode</div><div class="settings-row-desc">Reduce spacing throughout</div></div><label class="toggle"><input type="checkbox" id="compact-mode"><span class="toggle-slider"></span></label></div>
                            <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Show Animations</div><div class="settings-row-desc">Enable smooth transitions</div></div><label class="toggle"><input type="checkbox" id="animations" checked=""><span class="toggle-slider"></span></label></div>
                        </div>
                    </section>
                </div>

                <!-- Right Column: Notifications & Security -->
                <div>
                    <section class="settings-section">
                        <div class="card">
                            <h2 class="settings-title">Notifications</h2>
                            <p class="settings-desc">Choose what notifications you receive</p>
                            <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Email Notifications</div><div class="settings-row-desc">Receive updates via email</div></div><label class="toggle"><input type="checkbox" id="email-notif" checked=""><span class="toggle-slider"></span></label></div>
                            <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Push Notifications</div><div class="settings-row-desc">Real-time browser alerts</div></div><label class="toggle"><input type="checkbox" id="push-notif" checked=""><span class="toggle-slider"></span></label></div>
                            <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Project Updates</div><div class="settings-row-desc">When projects are updated</div></div><label class="toggle"><input type="checkbox" id="project-notif" checked=""><span class="toggle-slider"></span></label></div>
                            <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Team Mentions</div><div class="settings-row-desc">When someone mentions you</div></div><label class="toggle"><input type="checkbox" id="mention-notif" checked=""><span class="toggle-slider"></span></label></div>
                            <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Weekly Digest</div><div class="settings-row-desc">Weekly activity summary</div></div><label class="toggle"><input type="checkbox" id="digest-notif"><span class="toggle-slider"></span></label></div>
                        </div>
                    </section>

                    <section class="settings-section">
                        <div class="card">
                            <h2 class="settings-title">Security</h2>
                            <p class="settings-desc">Manage account security</p>
                            <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Two-Factor Authentication</div><div class="settings-row-desc">Extra layer of security</div></div><label class="toggle"><input type="checkbox" id="two-factor"><span class="toggle-slider"></span></label></div>
                            <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Login Alerts</div><div class="settings-row-desc">Notified of new logins</div></div><label class="toggle"><input type="checkbox" id="login-alerts" checked=""><span class="toggle-slider"></span></label></div>
                            <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Session Timeout</div><div class="settings-row-desc">Auto logout after inactivity</div></div><select class="form-control" style="width: auto; min-width: 130px;"><option>15 minutes</option><option selected="">30 minutes</option><option>1 hour</option><option>Never</option></select></div>
                            <div style="margin-top: 1.5rem; padding-top: 1.5rem; border-top: 1px solid var(--border-color);">
                                <h3 style="font-size: 0.9375rem; font-weight: 600; color: var(--text-primary); margin-bottom: 1rem;">Change Password</h3>
                                <div class="form-group"><label class="form-label">Current Password</label><input type="password" class="form-control" placeholder="Current password"></div>
                                <div class="form-group"><label class="form-label">New Password</label><input type="password" class="form-control" placeholder="New password"></div>
                                <button class="button button-primary">Update Password</button>
                            </div>
                            <div style="margin-top: 1.5rem; padding-top: 1.5rem; border-top: 1px solid var(--border-color);">
                                <h3 style="font-size: 0.9375rem; font-weight: 600; color: var(--danger); margin-bottom: 0.5rem;">Danger Zone</h3>
                                <button class="button button-danger">Delete Account</button>
                            </div>
                        </div>
                    </section>
                </div>
            </div>
        `
  return { start() {}, destroy() {} }
}
