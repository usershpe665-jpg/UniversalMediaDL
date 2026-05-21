/**
 * LunarMediaDL — UI Utilities
 * Header scroll, mobile nav, toast system
 * Author: Syawaliuz Octavian
 */

const UI = (() => {
  'use strict';

  // ── Header scroll ────────────────────────────────────────
  function initHeader() {
    const header = document.getElementById('header');
    if (!header) return;
    const onScroll = () => header.classList.toggle('scrolled', window.scrollY > 10);
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  // ── Mobile burger nav ─────────────────────────────────────
  function initBurger() {
    const burger = document.getElementById('navBurger');
    const links  = document.querySelector('.nav__links');
    if (!burger || !links) return;

    burger.addEventListener('click', () => {
      const expanded = burger.getAttribute('aria-expanded') === 'true';
      burger.setAttribute('aria-expanded', String(!expanded));
      links.classList.toggle('nav__links--open');
    });

    document.addEventListener('click', (e) => {
      if (!burger.contains(e.target) && !links.contains(e.target)) {
        burger.setAttribute('aria-expanded', 'false');
        links.classList.remove('nav__links--open');
      }
    });
  }

  // ── Toast notification system ─────────────────────────────
  function showToast(message, type = 'info', duration = 3500) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast--${type}`;

    const icons = {
      info:    '💬',
      success: '✅',
      error:   '❌',
      warning: '⚠️',
    };

    toast.innerHTML = `
      <span class="toast__icon">${icons[type] || icons.info}</span>
      <span class="toast__msg">${message}</span>
      <button class="toast__close" aria-label="Close">✕</button>
    `;

    toast.querySelector('.toast__close').addEventListener('click', () => dismissToast(toast));
    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => toast.classList.add('toast--visible'));

    // Auto dismiss
    const timer = setTimeout(() => dismissToast(toast), duration);
    toast._dismissTimer = timer;
  }

  function dismissToast(toast) {
    clearTimeout(toast._dismissTimer);
    toast.classList.remove('toast--visible');
    toast.classList.add('toast--hiding');
    toast.addEventListener('transitionend', () => toast.remove(), { once: true });
  }

  // ── Scroll reveal ─────────────────────────────────────────
  function initReveal() {
    if (!('IntersectionObserver' in window)) {
      document.querySelectorAll('[data-reveal]').forEach(el => el.classList.add('revealed'));
      return;
    }
    const obs = new IntersectionObserver((entries) => {
      entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('revealed'); obs.unobserve(e.target); } });
    }, { threshold: 0.1 });
    document.querySelectorAll('[data-reveal]').forEach(el => obs.observe(el));
  }

  // ── Init all ──────────────────────────────────────────────
  function init() {
    initHeader();
    initBurger();
    initReveal();
  }

  document.addEventListener('DOMContentLoaded', init);

  return { init, showToast };
})();
