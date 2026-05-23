import logoLight from '../assets/logo-light.png'
import logoDark from '../assets/logo-dark.png'

export default function Logo({ className = '' }) {
  return (
    <div className={`flex items-center ${className}`}>
      <img src={logoLight} alt="DeMaestro" className="h-9 w-auto block dark:hidden" />
      <img src={logoDark} alt="DeMaestro" className="h-9 w-auto hidden dark:block" />
    </div>
  )
}
