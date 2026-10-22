/* Progressive disclosure only; the server repeats every contract validation. */
(() => {
  const form = document.querySelector("[data-intake-form]");
  if (!form) return;
  const contract = JSON.parse(document.getElementById("intake-contract").textContent);
  const prior = JSON.parse(document.getElementById("intake-prior-answers").textContent);
  function answer(key) {
    const fields = [...form.elements].filter(el => el.name === key);
    if (!fields.length) return prior[key];
    if (fields[0].type === "checkbox") {
      const question = contract.questions.find(q => q.key === key);
      return question?.type === "consent" ? fields[0].checked : fields.filter(el => el.checked).map(el => el.value);
    }
    return fields[0].value;
  }
  function visible(rule) {
    if (!rule) return true;
    if (rule.all) return rule.all.every(visible);
    if (rule.any) return rule.any.some(visible);
    if (rule.op === "adult_non_student") {
      const dob = answer("dob"); if (!dob) return false;
      const today = new Date(), born = new Date(dob + "T12:00:00");
      const age = today.getFullYear() - born.getFullYear() - ((today.getMonth() < born.getMonth() ||
        (today.getMonth() === born.getMonth() && today.getDate() < born.getDate())) ? 1 : 0);
      return age >= 18 && answer("current_situation") !== "high_school";
    }
    const value = answer(rule.field);
    if (rule.op === "in") return rule.value.includes(value);
    if (rule.op === "contains") return Array.isArray(value) && value.includes(rule.value);
    return value === rule.value;
  }
  function refresh() {
    for (const q of contract.questions) {
      const box = form.querySelector('[data-question="' + q.key + '"]');
      if (!box) continue;
      box.hidden = !visible(q.rule);
      box.querySelectorAll("input, select, textarea, button").forEach(el => { el.disabled = box.hidden; });
    }
    for (const row of form.querySelectorAll("[data-experience-entry]")) {
      const area = row.querySelector('[data-experience-field="area"] select')?.value;
      const allowed = contract.area_skills[area] || [];
      row.querySelectorAll('[data-experience-field="skills"] input').forEach(el => {
        const label = el.closest("label"), item = label?.parentElement;
        el.disabled = !allowed.includes(el.value);
        if (el.disabled) el.checked = false;
        if (item) item.hidden = el.disabled;
      });
    }
  }
  const experience = form.querySelector('[data-question="experiences"]');
  const handsOn = form.querySelector('[data-question="hands_on"]');
  if (experience && handsOn) handsOn.after(experience);
  const list = form.querySelector("[data-experience-list]");
  const original = list?.firstElementChild?.cloneNode(true);
  form.addEventListener("change", refresh);
  form.addEventListener("click", event => {
    const add = event.target.closest("[data-add-experience]");
    const remove = event.target.closest("[data-remove-experience]");
    if (add && original) {
      const row = original.cloneNode(true), old = row.querySelector('[name="experience_ids"]').value;
      const fresh = crypto.randomUUID();
      row.querySelectorAll("[name], [id], label[for]").forEach(el => {
        for (const attr of ["name", "id", "for"]) if (el.hasAttribute(attr)) el.setAttribute(attr, el.getAttribute(attr).replaceAll(old, fresh));
      });
      row.querySelectorAll("input, textarea, select").forEach(el => {
        if (el.name === "experience_ids") el.value = fresh;
        else if (el.type === "checkbox") el.checked = false;
        else el.value = "";
      });
      list.append(row); refresh(); row.querySelector("select, input:not([type=hidden])")?.focus();
    }
    if (remove) {
      const row = remove.closest("[data-experience-entry]");
      if (list.children.length > 1) { row.remove(); form.querySelector("[data-add-experience]").focus(); }
      else { row.querySelectorAll("select, textarea, input:not([type=hidden])").forEach(el => { if (el.type === "checkbox") el.checked = false; else el.value = ""; }); refresh(); }
    }
  });
  refresh();
  document.getElementById("intake-errors")?.focus();
})();
