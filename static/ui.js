/* BarberBook UI helpers: light/dark theme + header tools container.
   Loaded with <script src="/static/ui.js" defer></script> on every page.
   Exposes window.BarberBookUI = { getTheme, setTheme, toggleTheme, getNavTools }. */
(function () {
    'use strict';

    var STORAGE_KEY = 'bb-theme';
    var root = document.documentElement;
    var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

    function readStored() {
        try {
            var v = window.localStorage.getItem(STORAGE_KEY);
            return v === 'light' || v === 'dark' ? v : null;
        } catch (e) {
            return null;
        }
    }

    function writeStored(value) {
        try {
            window.localStorage.setItem(STORAGE_KEY, value);
        } catch (e) { /* private mode / storage disabled: ignore */ }
    }

    function systemTheme() {
        return media && media.matches ? 'dark' : 'light';
    }

    function getTheme() {
        return root.dataset.theme === 'dark' ? 'dark' : 'light';
    }

    var SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>';
    var MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

    var toggleButton = null;
    var transitionTimer = null;

    function updateButton() {
        if (!toggleButton) return;
        var dark = getTheme() === 'dark';
        // Show the icon of the theme you will switch TO.
        toggleButton.innerHTML = dark ? SUN : MOON;
        var label = dark ? 'Switch to light theme' : 'Switch to dark theme';
        toggleButton.setAttribute('aria-label', label);
        toggleButton.title = label;
        toggleButton.setAttribute('aria-pressed', dark ? 'true' : 'false');
    }

    function applyTheme(theme, animate) {
        if (animate) {
            root.classList.add('theme-transition');
            clearTimeout(transitionTimer);
            transitionTimer = setTimeout(function () {
                root.classList.remove('theme-transition');
            }, 350);
        }
        root.dataset.theme = theme;
        updateButton();
        try {
            document.dispatchEvent(new CustomEvent('bb:themechange', { detail: { theme: theme } }));
        } catch (e) { /* very old browsers: ignore */ }
    }

    function setTheme(theme) {
        theme = theme === 'dark' ? 'dark' : 'light';
        writeStored(theme);
        applyTheme(theme, true);
    }

    function toggleTheme() {
        setTheme(getTheme() === 'dark' ? 'light' : 'dark');
    }

    // Apply right away (the inline snippet in <head> normally did this already).
    applyTheme(readStored() || systemTheme(), false);

    // Follow the OS setting while the user has not chosen explicitly.
    if (media) {
        var onSystemChange = function () {
            if (!readStored()) applyTheme(systemTheme(), true);
        };
        if (media.addEventListener) media.addEventListener('change', onSystemChange);
        else if (media.addListener) media.addListener(onSystemChange);
    }

    // Other tabs changing the theme.
    window.addEventListener('storage', function (event) {
        if (event.key === STORAGE_KEY) applyTheme(readStored() || systemTheme(), true);
    });

    var navTools = null;

    function getNavTools() {
        if (navTools && document.body && document.body.contains(navTools)) return navTools;
        navTools = document.querySelector('.nav-tools');
        if (navTools) return navTools;

        navTools = document.createElement('div');
        navTools.className = 'nav-tools';
        var header = document.querySelector('.navbar');
        if (header) {
            header.appendChild(navTools);
        } else {
            navTools.classList.add('nav-tools--floating');
            document.body.appendChild(navTools);
        }
        return navTools;
    }

    function initToggle() {
        if (!document.body || document.querySelector('.theme-toggle')) return;
        toggleButton = document.createElement('button');
        toggleButton.type = 'button';
        toggleButton.className = 'theme-toggle';
        toggleButton.addEventListener('click', toggleTheme);
        getNavTools().appendChild(toggleButton);
        updateButton();
    }

    window.BarberBookUI = {
        getTheme: getTheme,
        setTheme: setTheme,
        toggleTheme: toggleTheme,
        getNavTools: getNavTools
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initToggle);
    } else {
        initToggle();
    }
})();
