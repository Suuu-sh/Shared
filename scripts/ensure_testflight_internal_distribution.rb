#!/usr/bin/env ruby
require 'base64'
require 'json'
require 'net/http'
require 'openssl'
require 'optparse'
require 'uri'

class AppStoreConnectError < StandardError
  attr_reader :status, :body

  def initialize(status, body)
    @status = status
    @body = body
    super("App Store Connect API request failed with status #{status}")
  end
end

options = {
  group_name: ENV['TESTFLIGHT_INTERNAL_GROUP_NAME'].to_s.strip,
  tester_emails: ENV.fetch('TESTFLIGHT_INTERNAL_TESTER_EMAILS', '')
    .split(/[,\s;]+/)
    .map(&:strip)
    .reject(&:empty?),
  poll_attempts: Integer(ENV.fetch('TESTFLIGHT_BUILD_LOOKUP_ATTEMPTS', '20')),
  poll_interval: Integer(ENV.fetch('TESTFLIGHT_BUILD_LOOKUP_INTERVAL_SECONDS', '15')),
  notify_assigned_testers: ENV.fetch('TESTFLIGHT_NOTIFY_ASSIGNED_TESTERS', 'false') == 'true'
}

OptionParser.new do |opts|
  opts.banner = 'Usage: ensure_testflight_internal_distribution.rb --bundle-id BUNDLE_ID [options]'
  opts.on('--bundle-id BUNDLE_ID', 'Bundle identifier to configure') { |v| options[:bundle_id] = v }
  opts.on('--build-number NUMBER', 'Uploaded build number to attach to the group when needed') { |v| options[:build_number] = v }
  opts.on('--group-name NAME', 'Internal TestFlight group name (default: CI Internal)') { |v| options[:group_name] = v }
  opts.on('--tester-emails EMAILS', 'Comma-separated Apple ID emails to ensure in the internal group') do |v|
    options[:tester_emails] = v.split(/[,\s;]+/).map(&:strip).reject(&:empty?)
  end
  opts.on('--poll-attempts N', Integer, 'How many times to poll for the uploaded build (default: 20)') { |v| options[:poll_attempts] = v }
  opts.on('--poll-interval SECONDS', Integer, 'Seconds between build polling attempts (default: 15)') { |v| options[:poll_interval] = v }
  opts.on('--notify-assigned-testers', 'Send a TestFlight availability notification for the resolved build') { options[:notify_assigned_testers] = true }
end.parse!

options[:bundle_id] ||= ENV['APP_BUNDLE_ID']
abort('Provide --bundle-id or APP_BUNDLE_ID') if options[:bundle_id].to_s.empty?

key_id = ENV.fetch('ASC_KEY_ID')
issuer_id = ENV.fetch('ASC_ISSUER_ID')
private_key_path = ENV.fetch('ASC_PRIVATE_KEY_PATH')
private_key = OpenSSL::PKey.read(File.read(private_key_path))

def b64url(data)
  Base64.urlsafe_encode64(data).delete('=')
end

def der_to_jose(der_sig, size)
  asn1 = OpenSSL::ASN1.decode(der_sig)
  asn1.value.map(&:value).map { |i| i.to_s(2).rjust(size, "\x00") }.join
end

def token_for(key_id, issuer_id, private_key)
  header = { alg: 'ES256', kid: key_id, typ: 'JWT' }
  now = Time.now.to_i
  payload = {
    iss: issuer_id,
    iat: now,
    exp: now + 1200,
    aud: 'appstoreconnect-v1'
  }
  signing_input = [b64url(header.to_json), b64url(payload.to_json)].join('.')
  der_sig = private_key.sign('SHA256', signing_input)
  raw_sig = der_to_jose(der_sig, 32)
  [signing_input, b64url(raw_sig)].join('.')
end

TOKEN = token_for(key_id, issuer_id, private_key)

def request_json(method, path, params: {}, body: nil, expected_statuses: [200])
  uri = URI("https://api.appstoreconnect.apple.com#{path}")
  uri.query = URI.encode_www_form(params) unless params.empty?

  request_class = {
    'GET' => Net::HTTP::Get,
    'POST' => Net::HTTP::Post,
    'PATCH' => Net::HTTP::Patch
  }.fetch(method)

  request = request_class.new(uri)
  request['Authorization'] = "Bearer #{TOKEN}"
  request['Content-Type'] = 'application/json'
  request.body = JSON.generate(body) if body

  response = Net::HTTP.start(uri.host, uri.port, use_ssl: true) { |http| http.request(request) }
  parsed = response.body.to_s.empty? ? {} : JSON.parse(response.body)
  return parsed if expected_statuses.include?(response.code.to_i)

  raise AppStoreConnectError.new(response.code.to_i, parsed)
end

def get_json(path, params = {})
  request_json('GET', path, params: params, expected_statuses: [200])
end

def post_json(path, body, expected_statuses: [200, 201, 202, 204])
  request_json('POST', path, body: body, expected_statuses: expected_statuses)
end

def find_app(bundle_id)
  get_json('/v1/apps', {
    'filter[bundleId]' => bundle_id,
    'limit' => '2'
  }).fetch('data', []).first
end

def find_group(app_id, group_name)
  get_json('/v1/betaGroups', {
    'filter[app]' => app_id,
    'filter[isInternalGroup]' => 'true',
    'filter[name]' => group_name,
    'limit' => '200'
  }).fetch('data', []).find do |group|
    attrs = group.fetch('attributes', {})
    attrs['name'] == group_name && attrs['isInternalGroup'] == true
  end
end

def preferred_internal_group(app_id)
  groups = get_json('/v1/betaGroups', {
    'filter[app]' => app_id,
    'filter[isInternalGroup]' => 'true',
    'limit' => '200'
  }).fetch('data', [])

  groups.find { |group| group.dig('attributes', 'name') == 'App Store Connect Users' } ||
    groups.find { |group| group.dig('attributes', 'hasAccessToAllBuilds') == true } ||
    groups.first
end

def create_group(app_id, group_name)
  post_json('/v1/betaGroups', {
    data: {
      type: 'betaGroups',
      attributes: {
        name: group_name,
        isInternalGroup: true,
        hasAccessToAllBuilds: true,
        feedbackEnabled: true
      },
      relationships: {
        app: {
          data: {
            type: 'apps',
            id: app_id
          }
        }
      }
    }
  }, expected_statuses: [201]).fetch('data')
end

def find_tester(email, app_id: nil, group_id: nil)
  params = {
    'filter[email]' => email,
    'limit' => '2'
  }
  params['filter[apps]'] = app_id if app_id
  params['filter[betaGroups]'] = group_id if group_id
  direct_match = get_json('/v1/betaTesters', params).fetch('data', []).find do |tester|
    tester.dig('attributes', 'email').to_s.casecmp?(email)
  end
  return direct_match if direct_match

  return nil if app_id || group_id

  page_path = '/v1/betaTesters'
  page_params = {
    'limit' => '200',
    'sort' => 'email'
  }

  5.times do
    response = get_json(page_path, page_params)
    tester = response.fetch('data', []).find do |item|
      item.dig('attributes', 'email').to_s.casecmp?(email)
    end
    return tester if tester

    next_link = response.dig('links', 'next')
    break if next_link.to_s.empty?

    uri = URI(next_link)
    page_path = uri.path
    page_params = URI.decode_www_form(uri.query.to_s).to_h
  end

  nil
end

def create_tester(email)
  post_json('/v1/betaTesters', {
    data: {
      type: 'betaTesters',
      attributes: {
        email: email
      }
    }
  }, expected_statuses: [201]).fetch('data')
rescue AppStoreConnectError => error
  if error.status == 409
    tester = find_tester(email)
    return tester if tester
  end
  raise
end

def ensure_tester_in_group(email, tester_id, group_id)
  return if find_tester(email, group_id: group_id)

  begin
    post_json("/v1/betaGroups/#{group_id}/relationships/betaTesters", {
      data: [
        {
          type: 'betaTesters',
          id: tester_id
        }
      ]
    }, expected_statuses: [204])
  rescue AppStoreConnectError => error
    raise unless error.status == 409

    # App Store Connect returns 409 when the tester is already related to the
    # group. Treat that as an idempotent success so reruns do not fail after the
    # build upload has already succeeded.
    return if find_tester(email, group_id: group_id) || find_tester(email)

    raise
  end
end

def find_build(app_id, build_number, group_id: nil)
  params = {
    'filter[app]' => app_id,
    'filter[version]' => build_number,
    'limit' => '2'
  }
  params['filter[betaGroups]'] = group_id if group_id
  get_json('/v1/builds', params).fetch('data', []).find do |build|
    build.dig('attributes', 'version').to_s == build_number.to_s
  end
end

def latest_build(app_id)
  get_json('/v1/builds', {
    'filter[app]' => app_id,
    'sort' => '-uploadedDate',
    'limit' => '1'
  }).fetch('data', []).first
end

def wait_for_build(app_id, build_number, attempts:, interval:)
  attempts.times do |attempt|
    build = find_build(app_id, build_number)
    return build if build

    sleep(interval) if attempt < attempts - 1
  end

  nil
end

def ensure_build_in_group(app_id, build_number, group, attempts:, interval:)
  return nil if build_number.to_s.empty?

  group_id = group.fetch('id')
  existing_build = find_build(app_id, build_number, group_id: group_id)
  return existing_build if existing_build

  build = wait_for_build(
    app_id,
    build_number,
    attempts: attempts,
    interval: interval
  )

  raise "Build #{build_number} was not found in App Store Connect after waiting #{attempts * interval} seconds." if build.nil?

  post_json("/v1/betaGroups/#{group_id}/relationships/builds", {
    data: [
      {
        type: 'builds',
        id: build.fetch('id')
      }
    ]
  }, expected_statuses: [204])

  build
end

def assign_tester_to_build(build_id, tester_id)
  post_json("/v1/builds/#{build_id}/relationships/individualTesters", {
    data: [
      {
        type: 'betaTesters',
        id: tester_id
      }
    ]
  }, expected_statuses: [204])
  true
rescue AppStoreConnectError => error
  return false if error.status == 409
  raise
end

def notify_for_build(build_id)
  post_json('/v1/buildBetaNotifications', {
    data: {
      type: 'buildBetaNotifications',
      relationships: {
        build: {
          data: {
            type: 'builds',
            id: build_id
          }
        }
      }
    }
  }, expected_statuses: [201]).fetch('data')
end

app = find_app(options[:bundle_id])
abort("App with bundle ID #{options[:bundle_id]} was not found in App Store Connect.") if app.nil?

requested_group_name = options[:group_name]
group = if requested_group_name.empty?
  preferred_internal_group(app.fetch('id'))
else
  find_group(app.fetch('id'), requested_group_name)
end

group_created = false

if group.nil?
  fallback_group_name = requested_group_name.empty? ? 'CI Internal' : requested_group_name
  group = create_group(app.fetch('id'), fallback_group_name)
  group_created = true
end

testers = options[:tester_emails].map do |email|
  tester = find_tester(email, app_id: app.fetch('id')) || create_tester(email)
  ensure_tester_in_group(email, tester.fetch('id'), group.fetch('id'))
  {
    email: email,
    id: tester.fetch('id'),
    state: tester.dig('attributes', 'state'),
    invite_type: tester.dig('attributes', 'inviteType')
  }
end

assigned_build = nil
unless group.dig('attributes', 'hasAccessToAllBuilds') == true
  assigned_build = ensure_build_in_group(
    app.fetch('id'),
    options[:build_number],
    group,
    attempts: options[:poll_attempts],
    interval: options[:poll_interval]
  )
end

resolved_build = assigned_build
resolved_build ||= if options[:build_number].to_s.empty?
  latest_build(app.fetch('id'))
else
  wait_for_build(
    app.fetch('id'),
    options[:build_number],
    attempts: options[:poll_attempts],
    interval: options[:poll_interval]
  ) || find_build(app.fetch('id'), options[:build_number])
end

notification = nil
notification_error = nil
if options[:notify_assigned_testers] && resolved_build
  begin
    notification = notify_for_build(resolved_build.fetch('id'))
  rescue AppStoreConnectError => error
    notification_error = {
      status: error.status,
      body: error.body
    }
  end
end

individual_assignments = []
if resolved_build
  testers.each do |tester|
    individual_assignments << {
      email: tester[:email],
      tester_id: tester[:id],
      build_id: resolved_build.fetch('id'),
      created: assign_tester_to_build(resolved_build.fetch('id'), tester[:id])
    }
  end
end

puts JSON.pretty_generate({
  app: {
    id: app.fetch('id'),
    bundle_id: app.dig('attributes', 'bundleId'),
    name: app.dig('attributes', 'name')
  },
  group: {
    id: group.fetch('id'),
    name: group.dig('attributes', 'name'),
    created: group_created,
    has_access_to_all_builds: group.dig('attributes', 'hasAccessToAllBuilds'),
    is_internal_group: group.dig('attributes', 'isInternalGroup')
  },
  testers: testers,
  build_assignment: assigned_build && {
    id: assigned_build.fetch('id'),
    number: assigned_build.dig('attributes', 'version'),
    processing_state: assigned_build.dig('attributes', 'processingState')
  },
  resolved_build: resolved_build && {
    id: resolved_build.fetch('id'),
    number: resolved_build.dig('attributes', 'version'),
    processing_state: resolved_build.dig('attributes', 'processingState')
  },
  notification: notification && {
    id: notification.fetch('id'),
    type: notification.fetch('type')
  },
  notification_error: notification_error,
  individual_assignments: individual_assignments
})
