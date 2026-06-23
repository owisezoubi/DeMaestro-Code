/**
 * routes.js — public / no-auth variant.
 * No GUEST_NAV, AUTH_NAV, or ADMIN_NAV — only PUBLIC_NAV exists.
 * Every route is visible to all visitors; the Navbar reads PUBLIC_NAV only.
 *
 * NOTE: This file is replaced at generation time with project-specific routes.
 * It is the no-auth pattern the generator follows when auth_required = false.
 */
import { Home } from "lucide-react"

export const ROUTES = [
  {
    path: "/",
    label: "Home",
    icon: Home,
    show_in_nav: true,
    requires: null,
  },
]

export const PUBLIC_NAV = ROUTES.filter(r => r.show_in_nav)
// No GUEST_NAV, AUTH_NAV, or ADMIN_NAV — app is fully public.
