import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { useDropzone } from 'react-dropzone'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { FileText, Upload, X } from 'lucide-react'

import { createProject } from '../api/projects'
import { submitTextRequirements, submitPdfRequirements } from '../api/requirements'
import { triggerStructuring } from '../api/structure'

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function NewProject() {
  const navigate = useNavigate()
  const qc = useQueryClient()

  // Persists across retries within one page mount — avoids double-creating a project.
  const projectIdRef = useRef(null)

  const [activeTab, setActiveTab] = useState('text')
  const [pdfFile, setPdfFile] = useState(null)
  const [pdfError, setPdfError] = useState(null)

  const {
    register,
    handleSubmit,
    watch,
    setError,
    clearErrors,
    formState: { errors },
  } = useForm({ defaultValues: { name: '', content: '' } })

  const content = watch('content')

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: { 'application/pdf': ['.pdf'] },
    maxFiles: 1,
    maxSize: 10 * 1024 * 1024,
    onDrop: (accepted) => {
      if (accepted.length > 0) {
        setPdfFile(accepted[0])
        setPdfError(null)
      }
    },
    onDropRejected: (rejections) => {
      const msg = rejections[0]?.errors[0]?.message || 'File rejected'
      toast.error(msg)
    },
  })

  const submitMut = useMutation({
    mutationFn: async ({ name, mode, content, file }) => {
      // Step 1: create project (skip on retry if already created)
      if (!projectIdRef.current) {
        const project = await createProject(name)
        projectIdRef.current = project.id
      }
      const id = projectIdRef.current

      // Step 2: submit requirements
      if (mode === 'text') {
        await submitTextRequirements(id, content)
      } else {
        await submitPdfRequirements(id, file)
      }
    },
    onSuccess: async () => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      toast.success('Project created!')
      const projectId = projectIdRef.current
      try {
        await triggerStructuring(projectId)
      } catch (err) {
        toast.error(err.friendlyMessage || 'Failed to start analysis. You can retry from the project page.')
      }
      navigate(`/projects/${projectId}`)
    },
    onError: (err) => {
      toast.error(err.friendlyMessage || 'Something went wrong. Please try again.')
    },
  })

  function switchTab(tab) {
    setActiveTab(tab)
    clearErrors('content')
    setPdfError(null)
  }

  function onSubmit(data) {
    if (activeTab === 'text' && data.content.length < 10) {
      setError('content', { message: 'Content must be at least 10 characters' })
      return
    }
    if (activeTab === 'pdf' && !pdfFile) {
      setPdfError('Please select a PDF file')
      return
    }
    setPdfError(null)
    submitMut.mutate({ name: data.name, mode: activeTab, content: data.content, file: pdfFile })
  }

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900">


      {/* Main */}
      <main className="max-w-2xl mx-auto px-4 py-10">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">New project</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            Give your project a name and describe what you want to build.
          </p>
        </div>

        <div className="card">
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-6" noValidate>
            {/* Project name */}
            <div>
              <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                Project name <span className="text-red-500">*</span>
              </label>
              <input
                {...register('name', {
                  required: 'Project name is required',
                  maxLength: { value: 80, message: 'Name must be 80 characters or fewer' },
                })}
                className="input"
                placeholder="My awesome app"
                autoFocus
              />
              {errors.name && (
                <p className="text-xs text-red-600 mt-1">{errors.name.message}</p>
              )}
            </div>

            {/* Tab toggle */}
            <div>
              <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                Requirements
              </label>
              <div className="flex rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-700 p-1">
                <button
                  type="button"
                  onClick={() => switchTab('text')}
                  className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                    activeTab === 'text'
                      ? 'bg-white dark:bg-slate-600 text-primary-700 dark:text-primary-300 shadow-sm border border-slate-200 dark:border-slate-500'
                      : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200'
                  }`}
                >
                  <FileText className="w-4 h-4" />
                  Type requirements
                </button>
                <button
                  type="button"
                  onClick={() => switchTab('pdf')}
                  className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                    activeTab === 'pdf'
                      ? 'bg-white dark:bg-slate-600 text-primary-700 dark:text-primary-300 shadow-sm border border-slate-200 dark:border-slate-500'
                      : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200'
                  }`}
                >
                  <Upload className="w-4 h-4" />
                  Upload PDF
                </button>
              </div>
            </div>

            {/* Text tab */}
            {activeTab === 'text' && (
              <div>
                <textarea
                  {...register('content')}
                  className="input min-h-[200px] resize-y"
                  placeholder="Describe the app you want to build — e.g. 'A recipe app where users can save and tag recipes, search by ingredient, and share favorites with friends.'"
                />
                <div className="flex items-start justify-between mt-1">
                  {errors.content ? (
                    <p className="text-xs text-red-600">{errors.content.message}</p>
                  ) : (
                    <span />
                  )}
                  <p
                    className={`text-xs tabular-nums ${
                      content.length > 45000 ? 'text-amber-600' : 'text-slate-400 dark:text-slate-500'
                    }`}
                  >
                    {content.length.toLocaleString()}/50,000
                  </p>
                </div>
              </div>
            )}

            {/* PDF tab */}
            {activeTab === 'pdf' && (
              <div className="space-y-3">
                {!pdfFile ? (
                  <div
                    {...getRootProps()}
                    className={`border-2 border-dashed rounded-lg p-10 text-center cursor-pointer transition-colors ${
                      isDragActive
                        ? 'border-primary-400 bg-primary-50 dark:bg-primary-900/20'
                        : 'border-slate-300 dark:border-slate-600 hover:border-primary-300 hover:bg-slate-50 dark:hover:bg-slate-700'
                    }`}
                  >
                    <input {...getInputProps()} />
                    <Upload className="w-8 h-8 text-slate-400 mx-auto mb-3" />
                    <p className="text-sm font-medium text-slate-600 dark:text-slate-400">
                      {isDragActive
                        ? 'Drop it here…'
                        : 'Drag and drop a PDF here, or click to browse'}
                    </p>
                    <p className="text-xs text-slate-400 dark:text-slate-500 mt-1">PDF only · max 10 MB</p>
                  </div>
                ) : (
                  <div className="flex items-center gap-3 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-700 p-3">
                    <FileText className="w-5 h-5 text-primary-600 flex-shrink-0" />
                    <span className="text-sm text-slate-700 dark:text-slate-300 flex-1 truncate">{pdfFile.name}</span>
                    <span className="text-xs text-slate-500 dark:text-slate-400 flex-shrink-0">
                      {formatBytes(pdfFile.size)}
                    </span>
                    <button
                      type="button"
                      onClick={() => setPdfFile(null)}
                      className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 flex-shrink-0"
                      aria-label="Remove file"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                )}
                {pdfError && <p className="text-xs text-red-600">{pdfError}</p>}
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={submitMut.isPending}
              className="btn-primary w-full"
            >
              {submitMut.isPending ? 'Creating…' : 'Create project & submit requirements'}
            </button>
          </form>
        </div>
      </main>
    </div>
  )
}
