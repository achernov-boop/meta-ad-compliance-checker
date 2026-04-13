// Vercel Blob client-upload token handler.
//
// The browser calls this endpoint via `@vercel/blob/client`'s `upload()` helper,
// which triggers this handler to mint a short-lived signed token.
// The browser then uploads the video directly to Vercel Blob (bypassing our
// 4.5MB serverless body limit), and only the resulting Blob URL is sent to
// /api/analyze.
//
// Env required: BLOB_READ_WRITE_TOKEN (auto-provisioned when Blob is enabled
// for the Vercel project).

import { handleUpload } from '@vercel/blob/client';

export const config = {
  runtime: 'nodejs',
};

export default async function handler(request, response) {
  if (request.method !== 'POST') {
    response.status(405).json({ error: 'Method not allowed' });
    return;
  }

  // Vercel Node.js runtime provides `request.body` pre-parsed for JSON requests.
  const body = request.body;

  try {
    const jsonResponse = await handleUpload({
      request,
      body,
      onBeforeGenerateToken: async (pathname) => {
        return {
          allowedContentTypes: [
            'video/mp4',
            'video/quicktime',
            'video/webm',
            'video/x-matroska',
          ],
          maximumSizeInBytes: 200 * 1024 * 1024, // 200 MB cap
          addRandomSuffix: true,
        };
      },
      onUploadCompleted: async () => {
        // No-op: we pick up the URL from the client-side upload() return value
        // and pass it to /api/analyze directly. No webhook state needed.
      },
    });

    response.status(200).json(jsonResponse);
  } catch (error) {
    response.status(400).json({ error: error.message || 'Upload token error' });
  }
}
