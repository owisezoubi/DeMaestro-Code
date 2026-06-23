/**
 * Single source of truth for app routes.
 * Both App.jsx (route mounting) and Navbar.jsx (nav links) read from this.
 * Adding a new page = add one entry here = it appears in nav automatically.
 *
 * Fields:
 *   path        — react-router path string
 *   label       — text shown in the navbar link
 *   icon        — lucide-react icon component (optional; null = no icon)
 *   show_in_nav — true to surface in the navbar
 *   requires    — controls which users see this link:
 *                   null    = everyone (public route)
 *                   "guest" = only logged-out users (Login, Register)
 *                   "auth"  = only logged-in users (Dashboard, Account)
 *                   "admin" = only users with role === "admin"
 *
 * NOTE: This file is replaced at generation time with project-specific routes
 * derived from the blueprint. The structure and filter exports below are the
 * canonical pattern the generator follows.
 */
import { Home, LogIn, UserPlus } from "lucide-react"

export const ROUTES = [
  {
    path: "/",
    label: "Home",
    icon: Home,
    show_in_nav: true,
    requires: null,
  },
  {
    path: "/login",
    label: "Login",
    icon: LogIn,
    show_in_nav: true,
    requires: "guest",
  },
  {
    path: "/register",
    label: "Register",
    icon: UserPlus,
    show_in_nav: true,
    requires: "guest",
  },
]

export const PUBLIC_NAV = ROUTES.filter(r => r.show_in_nav && r.requires === null)
export const GUEST_NAV  = ROUTES.filter(r => r.show_in_nav && r.requires === "guest")
export const AUTH_NAV   = ROUTES.filter(r => r.show_in_nav && r.requires === "auth")
export const ADMIN_NAV  = ROUTES.filter(r => r.show_in_nav && r.requires === "admin")
