(() => {
  const fileInput = document.querySelector("#resume");
  const fileName = document.querySelector("[data-file-name]");
  if (fileInput && fileName) {
    fileInput.addEventListener("change", () => {
      fileName.textContent = fileInput.files?.[0]?.name || "No file selected";
    });
  }

  document.querySelectorAll("[data-role-choice]").forEach((link) => {
    link.addEventListener("click", () => {
      const select = document.querySelector("#position");
      if (select) select.value = link.dataset.roleChoice || "";
    });
  });

  const form = document.querySelector("[data-application-form]");
  if (form) {
    form.addEventListener("submit", () => {
      const button = form.querySelector("[data-submit-button]");
      const label = form.querySelector("[data-submit-label]");
      if (button) {
        button.setAttribute("aria-busy", "true");
        button.disabled = true;
      }
      if (label) label.textContent = "Sending application…";
    });
  }

  const errorSummary = document.querySelector("[data-error-summary]");
  if (errorSummary) {
    errorSummary.focus();
    errorSummary.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  const aiForm = document.querySelector("[data-ai-form]");
  if (aiForm) {
    aiForm.addEventListener("submit", () => {
      const button = aiForm.querySelector("[data-ai-submit]");
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.textContent = "Analyzing…";
      }
    });
  }

  const deleteForm = document.querySelector("[data-delete-form]");
  if (deleteForm) {
    const referenceInput = deleteForm.querySelector("[data-delete-reference]");
    const referenceFeedback = deleteForm.querySelector("[data-reference-match]");
    const normaliseReference = (value) =>
      String(value || "")
        .normalize("NFKC")
        .replace(/[\u2010-\u2015\u2212\uFE58\uFE63\uFF0D]/g, "-")
        .replace(/[\s\u200B-\u200D\u2060\uFEFF]+/g, "")
        .toUpperCase();
    const expectedReference = normaliseReference(deleteForm.dataset.referenceCode);
    const referenceMatches = () =>
      Boolean(referenceInput?.value) &&
      normaliseReference(referenceInput.value) === expectedReference;
    const updateReferenceFeedback = () => {
      if (!referenceInput) return false;
      const matches = referenceMatches();
      const hasValue = Boolean(referenceInput.value.trim());
      referenceInput.setCustomValidity(
        hasValue && !matches ? "Enter the reference code shown above." : "",
      );
      if (referenceFeedback) {
        referenceFeedback.textContent = matches
          ? "Reference code matched."
          : "Spaces, letter case, and copied dash variants are accepted.";
        referenceFeedback.classList.toggle("reference-match-success", matches);
      }
      return matches;
    };
    referenceInput?.addEventListener("input", updateReferenceFeedback);

    deleteForm.addEventListener("submit", (event) => {
      if (!updateReferenceFeedback()) {
        event.preventDefault();
        referenceInput?.reportValidity();
        referenceInput?.focus();
        return;
      }
      if (!window.confirm("Permanently delete this application, its AI analyses, and resume?")) {
        event.preventDefault();
        return;
      }
      const button = deleteForm.querySelector("button[type='submit']");
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.textContent = "Deleting…";
      }
    });
  }

  const modelContext = document.modelContext;
  if (modelContext?.registerTool) {
    const controller = new AbortController();
    const roles = [
      "Campus Cafeteria Data Collector",
      "AI Search & Recommendation Engineer",
    ];
    Promise.resolve(
      modelContext.registerTool(
        {
          name: "start_bite_hunt_application",
          title: "Start Bite Hunt application",
          description:
            "Choose a listed role and stage contact details in the visible application form. This does not upload a resume or submit the application.",
          inputSchema: {
            type: "object",
            properties: {
              role: { type: "string", enum: roles },
              fullName: { type: "string", maxLength: 100 },
              email: { type: "string", maxLength: 254 },
              phone: { type: "string", maxLength: 40 },
              note: { type: "string", maxLength: 2000 },
            },
            required: ["role"],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false, untrustedContentHint: false },
          async execute(input) {
            if (!input || typeof input !== "object" || !roles.includes(input.role)) {
              throw new Error("Choose one of the listed Bite Hunt roles.");
            }
            const fields = {
              position: input.role,
              full_name: input.fullName,
              email: input.email,
              phone: input.phone,
              cover_note: input.note,
            };
            Object.entries(fields).forEach(([name, value]) => {
              if (value === undefined) return;
              const element = document.querySelector(`[name="${name}"]`);
              if (!element) throw new Error("The application form is unavailable.");
              element.value = String(value);
              element.dispatchEvent(new Event("input", { bubbles: true }));
              element.dispatchEvent(new Event("change", { bubbles: true }));
            });
            document.querySelector("#apply")?.scrollIntoView({ behavior: "smooth" });
            document.querySelector("#resume")?.focus();
            return {
              status: "staged",
              role: input.role,
              nextStep: "The applicant must attach a resume, review consent, and submit the visible form.",
            };
          },
        },
        { signal: controller.signal },
      ),
    ).catch(() => {});
  }
})();
