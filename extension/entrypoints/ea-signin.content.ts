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

    // Visual marker so we can verify the content script loaded
    const marker = document.createElement('div');
    marker.id = 'op-seller-signin-marker';
    marker.style.cssText = 'position:fixed;top:0;left:0;background:lime;color:black;padding:5px 10px;z-index:99999;font-size:12px;font-weight:bold;';
    marker.textContent = 'OP Seller Signin CS loaded';
    document.body.appendChild(marker);

    chrome.storage.local.get(['algoCredentials'], (stored: Record<string, any>) => {
      const creds = stored.algoCredentials as { email: string; password: string } | undefined;

      marker.textContent += ' | creds=' + (creds ? 'YES' : 'NO');

      if (!creds) {
        marker.textContent += ' | SKIPPED';
        return;
      }

      // Wait for the page to fully render (jQuery + form elements)
      setTimeout(() => {
        fillAndSubmit(creds.email, creds.password);
      }, 2000);
    });

    function fillAndSubmit(email: string, password: string) {
      const passwordInput = document.querySelector('input[type="password"]') as HTMLInputElement | null;
      const emailInput = document.querySelector('#email') as HTMLInputElement | null;

      // The signin page has BOTH inputs on every step but hides the inactive one.
      // Check visibility to determine which step we're actually on.
      const passwordVisible = passwordInput && passwordInput.offsetWidth > 0 && passwordInput.offsetHeight > 0;

      marker.textContent += ' | pwVis=' + passwordVisible + ' em=' + !!emailInput;

      if (passwordVisible) {
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
