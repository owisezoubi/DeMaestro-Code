import { Link } from 'react-router-dom'
import logoLight from '../assets/logo-light.png'
import logoDark from '../assets/logo-dark.png'

export default function Logo({ className = '', imgClassName = 'h-11 w-auto', to = '/welcome', linked = true }) {
  const imgs = (
    <>
      <img src={logoLight} alt="DeMaestro" className={`${imgClassName} block dark:hidden`} />
      <img src={logoDark} alt="DeMaestro" className={`${imgClassName} hidden dark:block`} />
    </>
  )
  if (!linked) {
    return <div className={`flex items-center ${className}`}>{imgs}</div>
  }
  return (
    <Link to={to} className={`flex items-center cursor-pointer hover:opacity-80 transition-opacity ${className}`}>
      {imgs}
    </Link>
  )
}
