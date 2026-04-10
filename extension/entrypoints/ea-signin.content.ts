/**
 * Content script for EA sign-in page (signin.ea.com).
 *
 * Auto-fills email/password when credentials are configured.
 * Reads credentials from chrome.storage.local, then injects a <script>
 * tag to run jQuery-based form filling in the page's MAIN world.
 *
 * Runs on every signin.ea.com page load. If credentials exist, fills the form.
 */

export default defineContentScript({
  matches: ['https://signin.ea.com/*'],
  runAt: 'document_idle',
  main() {
    console.log('[OP Seller Signin] Content script loaded on:', window.location.href);

    chrome.storage.local.get(['algoCredentials'], (stored: Record<string, any>) => {
      const creds = stored.algoCredentials as { email: string; password: string } | undefined;

      console.log('[OP Seller Signin] Has credentials:', !!creds);

      if (!creds) {
        console.log('[OP Seller Signin] No credentials configured — skipping auto-fill');
        return;
      }

      // Wait for the page to fully render (jQuery + form elements)
      setTimeout(() => {
        fillAndSubmit(creds.email, creds.password);
      }, 2000);
    });

    function fillAndSubmit(email: string, password: string) {
      const passwordInput = document.querySelector('input[type="password"]');
      const emailInput = document.querySelector('#email') as HTMLInputElement | null;

      if (passwordInput) {
        console.log('[OP Seller Signin] Password page detected — filling password');
        injectMainWorldScript(`
          (function() {
            var jq = window.$ || window.jQuery;
            if (!jq) { console.error('[OP Seller Signin] No jQuery on page'); return; }
            jq('input[type="password"]').val(${JSON.stringify(password)}).trigger('input').trigger('change');
            console.log('[OP Seller Signin] Password set, clicking Sign In...');
            setTimeout(function() {
              jq('#logInBtn').trigger('mousedown').trigger('mouseup').trigger('click');
            }, 500);
          })();
        `);
      } else if (emailInput) {
        console.log('[OP Seller Signin] Email page detected — filling email:', email);
        injectMainWorldScript(`
          (function() {
            var jq = window.$ || window.jQuery;
            if (!jq) { console.error('[OP Seller Signin] No jQuery on page'); return; }
            jq('#email').val(${JSON.stringify(email)}).trigger('input').trigger('change');
            console.log('[OP Seller Signin] Email set to: ' + jq('#email').val() + ', clicking Next...');
            setTimeout(function() {
              jq('#logInBtn').trigger('mousedown').trigger('mouseup').trigger('click');
            }, 500);
          })();
        `);
      } else {
        console.log('[OP Seller Signin] No email or password input found on page');
      }
    }

    function injectMainWorldScript(code: string) {
      const script = document.createElement('script');
      script.textContent = code;
      document.documentElement.appendChild(script);
      script.remove();
    }
  },
});
