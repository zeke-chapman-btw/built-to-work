(() => {
  'use strict';
  const form = document.querySelector('[data-staff-member-form]');
  if (!form || !form.elements.login_mode) return;
  const personal = new Set(['first_name', 'last_name', 'email', 'phone', 'street_address', 'city', 'state', 'zip_code', 'shirt_size', 'is_active', 'internal_notes']);
  const newAccount = new Set(['username', 'login_email', 'roles', 'send_invitation']);
  function update() {
    const mode = form.elements.login_mode.value;
    const existingProfile = Boolean(form.elements.existing_profile.value);
    form.querySelectorAll('[data-staff-field]').forEach(wrapper => {
      const name = wrapper.dataset.staffField;
      const hide = (personal.has(name) && existingProfile) ||
        (newAccount.has(name) && mode !== 'new') || (name === 'existing_account' && mode !== 'existing');
      wrapper.hidden = hide;
      wrapper.querySelectorAll('input, select, textarea').forEach(input => { input.disabled = hide; });
    });
  }
  form.elements.login_mode.addEventListener('change', update);
  form.elements.existing_profile.addEventListener('change', update);
  update();
})();
